# DBus Connection Management
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import os
import asyncio
import pathlib
import logging
import dbus_next
from dbus_next.aio import MessageBus, ProxyInterface
from dbus_next.constants import BusType, NameFlag
from dbus_next.service import ServiceInterface, method
from dbus_next import Message, MessageType
import json

# Annotation imports
from typing import (
    TYPE_CHECKING,
    List,
    Optional,
    Any,
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

STAT_PATH = "/proc/self/stat"
DOC_URL = (
    "https://moonraker.readthedocs.io/en/latest/"
    "installation/#policykit-permissions"
)

class DBus:
    MethodExecute = "Execute"
    
    class Signal:
        Object = "/org/qidi/signal"
        Interface = "org.qidi.signal"
    class Klipper:
        Service = "org.qidi.klipper"
        Object = "/org/qidi/klipper"
        Interface = "org.qidi.klipper"

    class Moonraker:
        Service = "org.qidi.moonraker"
        Object = "/org/qidi/moonraker"
        Interface = "org.qidi.moonraker"

    class QidiClient:
        Service = "org.qidi.qidi-client"
        Object = "/org/qidi/qidi_client"
        Interface = "org.qidi.qidi_client"

class MoonrakerInterface(ServiceInterface):
    def __init__(self, name, handlers):
        super().__init__(name)
        self.handlers = handlers

    # 对应 C++ 的 SD_BUS_METHOD(DBus::MethodExecute, "s", "s", ...)
    # 's' 表示输入参数是字符串，returns='s' 表示返回也是字符串
    @method()
    async def Execute(self, input_str: 's') -> 's':
        try:
            # 1. 解析 JSON 负载
            data = json.loads(input_str)
            method_name = data.get("method", "")
            payload = data.get("payload", {})
            handler = self.handlers.get(method_name)
            
            if handler:
                # 执行注册的回调
                result = await handler(payload) if asyncio.iscoroutinefunction(handler) else handler(payload)
            else:
                result = {"error": f"Method not found: {method_name}"}
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": "JsonError", "details": str(e)})
        
class DbusManager:
    Variant = dbus_next.Variant
    DbusError = dbus_next.errors.DBusError
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.bus: Optional[MessageBus] = None
        self.polkit: Optional[ProxyInterface] = None
        self.warned: bool = False
        st_path = pathlib.Path(STAT_PATH)
        self.polkit_subject: List[Any] = []
        if not st_path.is_file():
            return
        proc_data = st_path.read_text()
        start_clk_ticks = int(proc_data.split()[21])
        self.polkit_subject = [
            "unix-process",
            {
                "pid": dbus_next.Variant("u", os.getpid()),
                "start-time": dbus_next.Variant("t", start_clk_ticks)
            }
        ]

        self.callbacks = {} 
        self.signal_handlers = {}

    def is_connected(self) -> bool:
        return self.bus is not None and self.bus.connected

    async def component_init(self) -> None:
        try:
            self.bus = MessageBus(bus_type=BusType.SYSTEM)
            await self.bus.connect() 
            reply = await self.bus.request_name(
                DBus.Moonraker.Service, 
                NameFlag.REPLACE_EXISTING
            )
            logging.info(f"Service registration result: {reply}")

            match_rule = (
                f"type='signal',"
                f"interface='{DBus.Signal.Interface}',"
                f"path='{DBus.Signal.Object}'"
            )
            await self.bus.call(
                Message(
                    destination='org.freedesktop.DBus',
                    path='/org/freedesktop/DBus',
                    interface='org.freedesktop.DBus',
                    member='AddMatch',
                    signature='s',
                    body=[match_rule]
                )
            )

            self.bus.add_message_handler(self._handle_incoming_message)

            self.register_method("ping", lambda p: {"result": "pong", "received": p, "identity": "moonraker"})
            self.register_method("query_methods_list", lambda p: {"methods": list(self.callbacks.keys())})

            self.register_signal_cb("online", lambda s, d: logging.info(f"[dbus] {s} 服务在线上，收到消息{d}"))

            self.interface = MoonrakerInterface(DBus.Moonraker.Interface, self.callbacks)
            self.bus.export(DBus.Moonraker.Object, self.interface)
            # 测试连接其他服务
            qidi_client_response = await self.send_to_qidi_client("query_methods_list",  {})
            logging.info(f"[dbus] qidi-client service test response: {qidi_client_response}")
            klipper = await self.send_to_klipper("query_methods_list",  {})
            logging.info(f"[dbus] klipper service test response: {klipper}")
            # 发送当前服务登录信号
            self.send_sigal("online", {})
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.info("Unable to Connect to D-Bus")
            return
        # Make sure that all required actions are register
        try:
            self.polkit = await self.get_interface(
                "org.freedesktop.PolicyKit1",
                "/org/freedesktop/PolicyKit1/Authority",
                "org.freedesktop.PolicyKit1.Authority")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.server.is_debug_enabled():
                logging.exception("Failed to get PolKit interface")
            else:
                logging.info(f"Failed to get PolKit interface: {e}")
            self.polkit = None

    async def check_permission(self,
                               action: str,
                               err_msg: str = ""
                               ) -> bool:
        if self.polkit is None:
            self.server.add_warning(
                "Unable to find DBus PolKit Interface, this suggests PolKit "
                "is not installed on your OS.",
                "dbus_polkit"
            )
            return False
        try:
            ret = await self.polkit.call_check_authorization(  # type: ignore
                self.polkit_subject, action, {}, 0, "")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._check_warned()
            self.server.add_warning(
                f"Error checking authorization for action [{action}]: {e}. "
                "This suggests that a dependency is not installed or "
                f"up to date. {err_msg}.")
            return False
        if not ret[0]:
            self._check_warned()
            self.server.add_warning(
                "Moonraker not authorized for PolicyKit action: "
                f"[{action}], {err_msg}")
        return ret[0]

    def _check_warned(self):
        if not self.warned:
            self.server.add_warning(
                f"PolKit warnings detected. See {DOC_URL} for instructions "
                "on how to resolve.")
            self.warned = True

    async def get_interface(self,
                            bus_name: str,
                            bus_path: str,
                            interface_name: str
                            ) -> ProxyInterface:
        ret = await self.get_interfaces(bus_name, bus_path,
                                        [interface_name])
        return ret[0]

    async def get_interfaces(self,
                             bus_name: str,
                             bus_path: str,
                             interface_names: List[str]
                             ) -> List[ProxyInterface]:
        if self.bus is None:
            raise self.server.error("Bus not avaialable")
        interfaces: List[ProxyInterface] = []
        introspection = await self.bus.introspect(bus_name, bus_path)
        proxy_obj = self.bus.get_proxy_object(bus_name, bus_path,
                                              introspection)
        for ifname in interface_names:
            intf = proxy_obj.get_interface(ifname)
            interfaces.append(intf)
        return interfaces

    async def close(self):
        if self.bus is not None and self.bus.connected:
            self.bus.disconnect()
            await self.bus.wait_for_disconnect()

    def _handle_incoming_message(self, msg: Message):
        # A. 改进后的错误处理
        if msg.message_type == MessageType.ERROR:
            # 只要总线上给 Moonraker 发了错误回复，都会在这里被抓到
            error_name = msg.error_name
            details = msg.body[0] if msg.body else "No details"
            # reply_serial 可以用来追踪是哪个请求失败了
            logging.error(f"[DBUS-BUS-ERROR] Name: {error_name} | Details: {details} | Serial: {msg.reply_serial}")
            return False
        # B. 信号捕获
        if msg.message_type == MessageType.SIGNAL:
            if msg.interface != DBus.Signal.Interface:
                return
            event = msg.member
            handlers_dict = self.signal_handlers.get(event)
            if not handlers_dict:
                return False

            try:
                data = json.loads(msg.body[0])
                sender = data.get("sender")
                payload = data.get("payload", {})
                targets = []
                for key in ["*", sender]:
                    for cb in handlers_dict.get(key, []):
                        if cb not in targets:
                            targets.append(cb)
                for cb in targets:
                    if asyncio.iscoroutinefunction(cb):
                        asyncio.create_task(cb(sender, payload))
                    else:
                        cb(sender, payload)
                        
            except Exception as e:
                logging.error(f"Failed to dispatch signal {event}: {e}")
            return False
        return False

    def register_signal_cb(self, event, callback, sender="*"):
        """动态注册信号回调"""
        event_dict = self.signal_handlers.setdefault(event, {})
        callbacks = event_dict.setdefault(sender, [])
        if callback not in callbacks:
            callbacks.append(callback)
        logging.info(f"[DBus] Moonraker registered signal: {event} from {sender}")

    def register_method(self, method_name, callback):
        if method_name in self.callbacks.keys():
            logging.warning(f"method [{method_name}] has been added")
            return
        self.callbacks[method_name] = callback
    
    def send_sigal(self, signal, payload):
        try:
            signal_body = json.dumps({
                "sender": DBus.Moonraker.Service,
                "payload": payload
            })
            msg = Message(
                message_type=MessageType.SIGNAL,
                path=DBus.Signal.Object,
                interface=DBus.Signal.Interface,
                member=signal, # 这里决定了 Klipper 端的 event 分发 Key
                signature='s',
                body=[signal_body]
            )
            self.bus.send(msg)
        except Exception as e:
            logging.exception(f"[DBUS] 发送信号失败")

    async def send_request(self, service, obj_path, interface, method_name, payload):
        try:
            request_body = json.dumps({
                "method": method_name,
                "payload": payload
            })
            reply = await self.bus.call(
                Message(
                    destination=service,
                    path=obj_path,
                    interface=interface,
                    member="Execute",     # 对应 C++ 里的 MethodExecute
                    signature='s',        # 签名：一个字符串
                    body=[request_body]   # 消息体：必须是列表
                )
            )

            # 3. 检查回复类型
            if reply.message_type == MessageType.METHOD_RETURN:
                # reply.body 是一个列表，取出第一个值（即返回的字符串）
                return json.loads(reply.body[0])
            else:
                return {"error": "DBus call returned error", "type": str(reply.message_type)}

        except Exception as e:
            logging.exception(f"[DBUS] Low-level call to {service} failed")
            return {"error": "Low-level DBus call failed", "details": str(e)}
    
    async def send_to_klipper(self, method, payload):
        """对应 C++ 的 sendToKlipper"""
        return await self.send_request(
            DBus.Klipper.Service,
            DBus.Klipper.Object,
            DBus.Klipper.Interface,
            method,
            payload
        )

    async def send_to_qidi_client(self, method, payload):
        """对应 C++ 调用 QidiClient"""
        return await self.send_request(
            DBus.QidiClient.Service,
            DBus.QidiClient.Object,
            DBus.QidiClient.Interface,
            method,
            payload
        )


def load_component(config: ConfigHelper) -> DbusManager:
    return DbusManager(config)
