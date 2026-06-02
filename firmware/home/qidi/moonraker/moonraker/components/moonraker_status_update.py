from __future__ import annotations
import logging
from ..utils import json_wrapper as jsonw
import copy

from ..common import (
    RequestType,
    WebRequest,
)

from typing import (
    TYPE_CHECKING,
    Dict,
    Any,
    Optional,
    List,
    Callable,
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    # 结构: { "模块名": ["字段1", "字段2"] }
    Subscription = Dict[str, Optional[List[str]]]

STAT_UPDATE_TIME = 1. 

class MoonrakerStatusUpdate:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.name = config.get_name()
        self.event_loop = self.server.get_event_loop()
        self.state: Dict[str, Any] = {}
        self._status_providers: Dict[str, Any] = {}
        
        # 核心：将订阅信息与 transport 对象关联，方便分发时直接调用 send_notification
        # 结构: { transport_id: {"transport": transport, "objects": Subscription} }
        self.subscriptions: Dict[int, Dict[str, Any]] = {}
        
        self.stat_update_timer = self.event_loop.register_timer(
            self._handle_status_update)
        
        # # 注册端点和通知名称
        # self.server.register_notification("moonraker_status:moonraker_status_update")
        self.server.register_endpoint("/server/moonraker_status/subscribe",  RequestType.POST, self._handle_moonraker_status_subscribe)

    async def component_init(self) -> None:
        def is_status_provider(name: str, comp: Any) -> bool:
            if name == self.name:
                return False
            return (hasattr(comp, "get_status") and 
                    callable(comp.get_status))
        
        # 使用你自定义的高效查找方法
        self._status_providers = self.server.lookup_components(is_status_provider)
        provider_names = list(self._status_providers.keys())
        logging.info(
            f"[MoonrakerStatusUpdate] Initialized. Found {len(provider_names)} "
            f"providers: {provider_names}"
        )
        self.stat_update_timer.start()

    async def _handle_moonraker_status_subscribe(self, web_request: WebRequest):
        params: Subscription = web_request.get('objects', {})
        # 获取支持订阅的 transport 实例
        transport = web_request.get_subscribable()
        transport_id = id(transport)
        
        # 1. 建立订阅关系
        self.subscriptions[transport_id] = {
            "transport": transport,
            "objects": params
        }
        
        # 2. 注册关闭回调（虽然核心 close 里有埋点，但这里双重保险不会导致崩溃）
        # 注意：Moonraker 的某些版本 transport 可能不支持 add_close_callback，
        # 依赖你在 websocket.py 里的埋点是更稳妥的。

        # 3. 构造即时响应（用户订阅瞬间拿到的最新数据）
        requested_status: Dict[str, Any] = {}
        for obj_name, fields in params.items():
            if obj_name in self.state:
                if not fields: # 订阅全部
                    requested_status[obj_name] = self.state[obj_name]
                else: # 过滤字段
                    requested_status[obj_name] = {
                        f: self.state[obj_name].get(f) 
                        for f in fields if f in self.state[obj_name]
                    }
            else:
                requested_status[obj_name] = {}

        logging.info(f"[MoonrakerStatusUpdate] Client {transport_id} subscribed to: {list(params.keys())}")
        return {"status": requested_status}

    async def _handle_status_update(self, eventtime: float) -> float:
        new_state: Dict[str, Any] = {}
        
        # 1. 采集状态
        for name, component in self._status_providers.items():
            try:
                # 必须 deepcopy 确保 self.state 比较逻辑的准确性
                new_state[name] = copy.deepcopy(component.get_status(eventtime))
            except Exception as e:
                logging.debug(f"[StatusUpdate] Failed to get status for {name}: {e}")
                continue

        # 2. 计算全局差异 (Delta)
        total_delta = self._get_delta(self.state, new_state)
        
        if total_delta:
            logging.info(f"[StatusUpdate] Global delta detected: {list(total_delta.keys())}")
            self.state = new_state
            
            # 3. 按连接订阅逻辑进行分发
            for transport_id, sub_data in self.subscriptions.items():
                transport = sub_data["transport"]
                sub_objects = sub_data["objects"]
                conn_delta = {}

                for obj_name, fields in sub_objects.items():
                    if obj_name in total_delta:
                        if not fields:
                            # 订阅了该对象所有字段
                            conn_delta[obj_name] = total_delta[obj_name]
                        else:
                            # 仅过滤订阅的特定字段
                            field_delta = {
                                f: total_delta[obj_name][f]
                                for f in fields if f in total_delta[obj_name]
                            }
                            if field_delta:
                                conn_delta[obj_name] = field_delta
                
                # 4. 执行发送并记录日志
                if conn_delta:
                    try:
                        transport.send_moonraker_status(conn_delta, eventtime)
                        logging.info(f"[StatusUpdate] SENT to {transport_id}: {list(conn_delta.keys())}")
                    except Exception as e:
                        logging.error(f"[StatusUpdate] SEND_FAILED to {transport_id}: {e}")
                else:
                    logging.debug(f"[StatusUpdate] SKIP {transport_id}: No subscribed changes")

        return eventtime + STAT_UPDATE_TIME

    def _get_delta(self, old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
        delta = {}
        for key, value in new.items():
            if key not in old:
                delta[key] = value
            elif isinstance(value, dict) and isinstance(old.get(key), dict):
                res = self._get_delta(old[key], value)
                if res:
                    delta[key] = res
            elif old[key] != value:
                delta[key] = value
        return delta

    def remove_subscription(self, transport: Any) -> None:
        t_id = id(transport)
        if t_id in self.subscriptions:
            logging.info(f"[MoonrakerStatusUpdate] Connection {t_id} closed, removing subscriptions.")
            self.subscriptions.pop(t_id, None)

def load_component(config: ConfigHelper) -> MoonrakerStatusUpdate:
    return MoonrakerStatusUpdate(config)