import socket
import errno
import logging
import os
import json
from dbus_fast.message import Message
from dbus_fast.constants import MessageType, NameFlag
from dbus_fast._private.unmarshaller import Unmarshaller

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

# --- 1. DBusConnection：底层通信引擎 ---
class DBusConnection:
    def __init__(self, reactor, bus_address, on_message_callback):
        self.reactor = reactor
        self.bus_address = bus_address
        self.on_message_callback = on_message_callback
        
        self.sock = None
        self.fd_handle = None
        self.unmarshaller = None
        self.send_buffer = b""
        self.is_blocking = False
        self._serial = 0
        
        self._connect()

    def _connect(self):
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(self.bus_address)
            self.sock.setblocking(1) 
            
            # SASL 握手
            uid_hex = str(os.getuid()).encode().hex()
            self.sock.sendall(b'\0AUTH EXTERNAL ' + uid_hex.encode() + b'\r\n')
            
            resp = b""
            while b'\r\n' not in resp:
                chunk = self.sock.recv(1)
                if not chunk: raise EOFError("Auth closed")
                resp += chunk
            
            if b'OK' not in resp:
                raise Exception(f"Auth failed: {resp}")
                
            self.sock.sendall(b'BEGIN\r\n')
            self.sock.setblocking(0) 
            
            self.unmarshaller = Unmarshaller(self.sock.makefile('rb', buffering=0))
            self.fd_handle = self.reactor.register_fd(
                self.sock.fileno(), self.process_received, self._do_send)
            
            logging.info("DBus connected to System Bus")
        except Exception:
            logging.exception("DBus connection failed")
            self.close()

    def send_message(self, message: Message):
        self._serial += 1
        message.serial = self._serial
        data = message.marshall() if hasattr(message, 'marshall') else message._marshall(0)
        self.send_buffer += data
        if not self.is_blocking:
            self._do_send()
        return self._serial

    def _do_send(self, eventtime=None):
        if not self.send_buffer: return self.reactor.NEVER
        try:
            sent = self.sock.send(self.send_buffer)
            self.send_buffer = self.send_buffer[sent:]
        except socket.error as e:
            if e.errno in [errno.EAGAIN, errno.EWOULDBLOCK]:
                self.reactor.set_fd_wake(self.fd_handle, read=True, write=True)
                self.is_blocking = True
            else:
                self.close()
        
        if not self.send_buffer and self.is_blocking:
            self.reactor.set_fd_wake(self.fd_handle, read=True, write=False)
            self.is_blocking = False
        return self.reactor.NEVER

    def process_received(self, eventtime):
        try:
            while True:
                msg = self.unmarshaller.unmarshall()
                if msg is None: break
                # 直接交给 Manager 处理原始 Message 对象
                self.reactor.register_callback(lambda et, m=msg: self.on_message_callback(m))
        except Exception:
            self.close()
        return self.reactor.NEVER

    def close(self):
        if self.fd_handle: self.reactor.unregister_fd(self.fd_handle)
        if self.sock: self.sock.close()
        self.fd_handle = self.sock = None

# --- 2. DBusServiceManager：逻辑与调度中心 ---
class DBusServiceManager:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        
        # 1. 回调管理
        self.method_handlers = {}  # 本地注册的方法: { "ping": func }
        self.pending_requests = {} # 等待回复的请求: { serial: callback_func }
        self.signal_handlers = {}
        
        # 2. 建立总线连接
        self.conn = DBusConnection(
            self.reactor, '/var/run/dbus/system_bus_socket', self._on_message_received)
        
        self.printer.add_object('dbus_manager', self)
        self._init_dbus()

    def _init_dbus(self):
        # 基础 DBus 握手
        self.conn.send_message(Message(
            destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
            interface='org.freedesktop.DBus', member='Hello'))
        
        self.conn.send_message(Message(
            destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
            interface='org.freedesktop.DBus', member='RequestName',
            signature='su', body=[DBus.Klipper.Service, NameFlag.REPLACE_EXISTING]))
        
        match_rule = (
            f"type='signal',"
            f"interface='{DBus.Signal.Interface}',"
            f"path='{DBus.Signal.Object}'"
        )
        self.conn.send_message(Message(
            destination='org.freedesktop.DBus', path='/org/freedesktop/DBus',
            interface='org.freedesktop.DBus', member='AddMatch',
            signature='s', body=[match_rule]
        ))

        # 注册一个默认测试方法
        self.register_local_method("ping", lambda params: {"result": "pong", "received": params, "identity": "klipper"})
        self.register_local_method("query_methods_list", lambda params: {"methods": list(self.method_handlers.keys())})
        # 注册一个服务上线检测方法
        self.register_signal_cb("online", lambda s, d: logging.info(f"[dbus] {s} 服务在线上，收到消息{d}"))
        # 测试与moonraker和qidi-client的连接
        self.call_moonraker("query_methods_list", {}, lambda p: logging.info(f"[dbus] mooonraker respond: {p}"))
        self.call_qidi_client("query_methods_list", {}, lambda p: logging.info(f"[dbus] qidi_client respond: {p}"))
        self.call_signal("online", {})

    def register_local_method(self, method_name, handler):
        """注册供远程调用的本地 JSON 方法"""
        if method_name in self.method_handlers:
            logging.warning(f"method [{method_name}] has been added")
            return
        self.method_handlers[method_name] = handler

    def register_signal_cb(self, event, callback, sender="*"):
        """
        显式注册函数
        :param event: 信号事件名 (如 "service_online")
        :param callback: 回调函数指针 (如 self._handle_online)
        :param sender: 匹配的发送者，默认为 "*" (通配)
        """
        event_dict = self.signal_handlers.setdefault(event, {})
        callbacks = event_dict.setdefault(sender, [])
        if callback not in callbacks:
            callbacks.append(callback)
        logging.info(f"DBus Signal Registered: {event} from {sender}")

    def call_remote(self, target, method, params=None, callback=None):
        """
        核心远程调用入口
        target: DBus.Moonraker 或 DBus.QidiClient 类
        method: 业务逻辑方法名 (JSON 内层)
        """
        payload = json.dumps({"method": method, "payload": params or {}})
        msg = Message(
            message_type=MessageType.METHOD_CALL,
            destination=target.Service,
            path=target.Object,
            interface=target.Interface,
            member=DBus.MethodExecute,
            signature='s',
            body=[payload]
        )
        serial = self.conn.send_message(msg)
        if callback:
            self.pending_requests[serial] = callback

    def call_signal(self, signal, params):
        payload = json.dumps({"sender": DBus.Klipper.Service, "payload": params or {}})
        msg = Message(
            message_type=MessageType.SIGNAL,
            path=DBus.Signal.Object,
            interface=DBus.Signal.Interface,
            member=signal, # 对应 D-Bus 协议中的 Member
            signature='s',
            body=[payload]
        )
        self.conn.send_message(msg)


    def _on_message_received(self, msg: Message):
        # A. 如果是对方返回的请求结果 (METHOD_RETURN)
        if msg.message_type == MessageType.METHOD_RETURN:
            callback = self.pending_requests.pop(msg.reply_serial, None)
            if callback:
                try:
                    result = json.loads(msg.body[0]) if msg.body else None
                    callback(result)
                except Exception:
                    logging.exception("Error processing callback for serial %d", msg.reply_serial)
            return

        # B. 如果是对方发来的方法调用 (METHOD_CALL)
        if msg.message_type == MessageType.METHOD_CALL:
            if msg.interface == DBus.Klipper.Interface and msg.member == DBus.MethodExecute:
                try:
                    data = json.loads(msg.body[0])
                    method_name = data.get("method")
                    params = data.get("payload", {})
                    
                    handler = self.method_handlers.get(method_name)
                    if handler:
                        # 执行本地逻辑并获取返回字典
                        response_data = handler(params)
                    else:
                        response_data = {"error":f"Method not found: {method_name}"}
                        logging.warning(f"No local handler for: {method_name}")
                    reply = Message.new_method_return(msg, 's', [json.dumps(response_data)])
                    self.conn.send_message(reply)
                except Exception as e:
                    logging.exception("Dispatch error")
        
        # C. 改进后的错误处理
        if msg.message_type == MessageType.ERROR:
            error_type = msg.error_name
            error_details = msg.body[0] if msg.body else "No details"
            callback = self.pending_requests.pop(msg.reply_serial, None)
            if callback:
                try: callback({"error": error_type, "details": error_details})
                except: pass
            logging.error(f"[DBus Error] Type: {error_type} | Details: {error_details}")
        
        # D. 信号捕获
        if msg.message_type == MessageType.SIGNAL:
            if msg.interface != DBus.Signal.Interface:
                return
            try:
                event = msg.member  
                event_handlers = self.signal_handlers.get(event)
                if not event_handlers:
                    return
                data = json.loads(msg.body[0])
                sender = data.get("sender")
                payload = data.get("payload", {})

                if not sender:
                    logging.warning(f"Signal {event} missing sender field")
                    return

                targets = []
                for key in ["*", sender]:
                    for cb in event_handlers.get(key, []):
                        if cb not in targets:
                            targets.append(cb)
                for cb in targets:
                    try:
                        cb(sender, payload)
                    except Exception as e:
                        logging.error(f"Callback error on {event}: {e}")

            except Exception as e:
                logging.error(f"Signal dispatch error: {e}")

    # --- 快捷封装 ---
    def call_moonraker(self, method, params=None, callback=None):
        self.call_remote(DBus.Moonraker, method, params, callback)

    def call_qidi_client(self, method, params=None, callback=None):
        self.call_remote(DBus.QidiClient, method, params, callback)

def load_config(config):
    return DBusServiceManager(config)