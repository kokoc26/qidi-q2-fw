# Helper for Moonraker to Klippy API calls.
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging
from ..utils import Sentinel
from ..common import WebRequest, APITransport, RequestType

# QIDI modified: Copy file to .cache when print start
import os
import shutil
import json
from .file_manager.file_manager import FileManager

import logging
import zipfile
import traceback

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Union,
    Optional,
    Dict,
    List,
    TypeVar,
    Mapping,
    Callable,
    Coroutine
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import UserInfo
    from .klippy_connection import KlippyConnection as Klippy
    from .file_manager.file_manager import FileManager
    Subscription = Dict[str, Optional[List[Any]]]
    SubCallback = Callable[[Dict[str, Dict[str, Any]], float], Optional[Coroutine]]
    _T = TypeVar("_T")

INFO_ENDPOINT = "info"
ESTOP_ENDPOINT = "emergency_stop"
LIST_EPS_ENDPOINT = "list_endpoints"
GC_OUTPUT_ENDPOINT = "gcode/subscribe_output"
GCODE_ENDPOINT = "gcode/script"
SUBSCRIPTION_ENDPOINT = "objects/subscribe"
STATUS_ENDPOINT = "objects/query"
OBJ_LIST_ENDPOINT = "objects/list"
REG_METHOD_ENDPOINT = "register_remote_method"

class KlippyAPI(APITransport):
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.klippy: Klippy = self.server.lookup_component("klippy_connection")
        self.fm: FileManager = self.server.lookup_component("file_manager")
        self.eventloop = self.server.get_event_loop()
        app_args = self.server.get_app_args()
        self.version = app_args.get('software_version')
        # Maintain a subscription for all moonraker requests, as
        # we do not want to overwrite them
        self.host_subscription: Subscription = {}
        self.subscription_callbacks: List[SubCallback] = []

        # Register GCode Aliases
        self.server.register_endpoint(
            "/printer/print/pause", RequestType.POST, self._gcode_pause
        )
        self.server.register_endpoint(
            "/printer/print/resume", RequestType.POST, self._gcode_resume
        )
        self.server.register_endpoint(
            "/printer/print/cancel", RequestType.POST, self._gcode_cancel
        )
        self.server.register_endpoint(
            "/printer/print/start", RequestType.POST, self._gcode_start_print
        )
        self.server.register_endpoint(
            "/printer/restart", RequestType.POST, self._gcode_restart
        )
        self.server.register_endpoint(
            "/printer/firmware_restart", RequestType.POST, self._gcode_firmware_restart
        )
        self.server.register_event_handler(
            "server:klippy_disconnect", self._on_klippy_disconnect
        )
        self.server.register_endpoint(
            "/printer/list_endpoints", RequestType.GET, self.list_endpoints
        )
        self.server.register_endpoint(
            "/printer/breakheater", RequestType.POST, self.breakheater
        )
        self.server.register_endpoint(
            "/printer/breakmacro", RequestType.POST, self.breakmacro
        )

    def _on_klippy_disconnect(self) -> None:
        self.host_subscription.clear()
        self.subscription_callbacks.clear()

    async def _gcode_pause(self, web_request: WebRequest) -> str:
        return await self.pause_print()

    async def _gcode_resume(self, web_request: WebRequest) -> str:
        return await self.resume_print()

    async def _gcode_cancel(self, web_request: WebRequest) -> str:
        return await self.cancel_print()

    async def _gcode_start_print(self, web_request: WebRequest) -> str:
        filename: str = web_request.get_str('filename')
        user = web_request.get_current_user()
        plateindex: str = web_request.get_str('plateindex', '1')
        with_check: str = web_request.get_str('with_check', '1')
        need_extract: bool = web_request.get_boolean('need_extract', True)
        return await self.start_print(filename, user=user, plateindex = plateindex, with_check = with_check, need_extract = need_extract)

    async def _gcode_restart(self, web_request: WebRequest) -> str:
        return await self.do_restart("RESTART")

    async def _gcode_firmware_restart(self, web_request: WebRequest) -> str:
        return await self.do_restart("FIRMWARE_RESTART")

    async def _send_klippy_request(
        self,
        method: str,
        params: Dict[str, Any],
        default: Any = Sentinel.MISSING,
        transport: Optional[APITransport] = None
    ) -> Any:
        try:
            req = WebRequest(method, params, transport=transport or self)
            result = await self.klippy.request(req)
        except self.server.error:
            if default is Sentinel.MISSING:
                raise
            result = default
        return result

    async def run_gcode(self,
                        script: str,
                        default: Any = Sentinel.MISSING
                        ) -> str:
        params = {'script': script}
        result = await self._send_klippy_request(
            GCODE_ENDPOINT, params, default)
        return result

    def copy_file_to_cache(self, origin, target):
        stat = os.statvfs("/")
        free_space = stat.f_frsize * stat.f_bfree
        filesize = os.path.getsize(os.path.join(origin))
        if (filesize < free_space):
            shutil.copy(origin, target)
        else:
            msg = "!! Insufficient disk space, unable to read the file."
            self.server.send_event("server:gcode_response", msg)
            raise self.server.error("Insufficient disk space, unable to read the file.", 500)

    async def start_print(
        self,
        filename: str,
        wait_klippy_started: bool = False,
        user: Optional[UserInfo] = None,
        plateindex: str = "1",
        with_check: str = "1",
        need_extract: bool = True,
    ) -> str:
        # WARNING: Do not call this method from within the following
        # event handlers when "wait_klippy_started" is set to True:
        # klippy_identified, klippy_started, klippy_ready, klippy_disconnect
        # Doing so will result in "wait_started" blocking for the specifed
        # timeout (default 20s) and returning False.
        # XXX - validate that file is on disk
        if(need_extract) :
            if filename[0] == '/':
                filename = filename[1:]
            # Escape existing double quotes in the file name
            filename = filename.replace("\"", "\\\"")
            homedir = os.path.expanduser("~")
            if os.path.split(filename)[0].split(os.path.sep)[0] != ".cache":
                base_path = os.path.join(homedir, "printer_data")
                gcodes_path = os.path.join(base_path, "gcodes")
                target = os.path.join(".cache", os.path.basename(filename))
                tempfile_target = os.path.join(".temp", os.path.basename(filename))
                cache_path = os.path.join(base_path, ".cache")
                tempfile_dir = os.path.join(base_path, ".temp")
                
                os.makedirs(cache_path, exist_ok=True)
                os.makedirs(tempfile_dir, exist_ok=True)
                
                for item in os.listdir(cache_path):
                    item_path = os.path.join(cache_path, item)
                    if os.path.isfile(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                        
                for item in os.listdir(tempfile_dir):
                    item_path = os.path.join(tempfile_dir, item)
                    if os.path.isfile(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                
                ext = os.path.splitext(filename)[-1].lower()
                if ext == '.3mf':
                    _3mf_path = os.path.join(cache_path, os.path.basename(filename))
                    _MODEL_PATH = "Metadata/plate_"+ plateindex +".gcode"
                    file_base_name = os.path.splitext(os.path.basename(filename))[0]
                    if os.path.splitext(file_base_name)[-1] == '.gcode':
                        dest_path = os.path.splitext(os.path.basename(filename))[0]
                    else:
                        dest_path = os.path.splitext(os.path.basename(filename))[0] + ".gcode"
                    tempfile_target = os.path.join(".temp", os.path.basename(dest_path))
                    
                    source_file = os.path.join(gcodes_path, filename)
                    target_file = os.path.join(base_path, target)
                    logging.info(f"开始复制3mf文件, filename = {filename}")
                    await self.eventloop.run_in_thread(self.copy_file_to_cache, source_file, target_file)
                    logging.info(f"3mf文件复制完成, filename = {filename}")

                    try:
                        with zipfile.ZipFile(_3mf_path) as zf:
                            namelist = zf.namelist()
                            logging.info(f"namelist == {namelist}")
                            if _MODEL_PATH in namelist:
                                # gcode_content = zf.read(_MODEL_PATH)
                                temp_target_path = os.path.join(base_path, tempfile_target)
                                logging.info(f"开始写入gcode文件, filename = {filename}")
                                # await self.eventloop.run_in_thread(
                                #     self._write_file_content, temp_target_path, gcode_content
                                # )
                                progress = 0
                                with zipfile.ZipFile(_3mf_path) as zf:

                                    file_info = zf.getinfo(_MODEL_PATH)
                                    total_size = file_info.file_size

                                    with zf.open(_MODEL_PATH) as src_file, open(temp_target_path, 'wb') as dest_file:
                                        chunk_size = 10 * 1024 * 1024  # 10 MB
                                        while True:
                                            chunk = src_file.read(chunk_size)
                                            if not chunk:
                                                break
                                            dest_file.write(chunk)
                                            progress += len(chunk)
                                            percent = (progress / total_size) * 100
                                logging.info(f"gcode文件写入完成, filename = {filename}")
                    except Exception:
                        logging.info(traceback.format_exc())
                else:
                    metadata = self.fm.gcode_metadata.metadata.get(filename, None)
                    self.copy_file_to_cache(os.path.join(gcodes_path, filename), os.path.join(base_path, target))
                    self.copy_file_to_cache(os.path.join(gcodes_path, filename), os.path.join(base_path, tempfile_target))
                    msg = "// metadata=" + json.dumps(metadata)
                    self.server.send_event("server:gcode_response", msg)
                    
            else:
                logging.info(f"开始复制缓存文件, filename = {filename}")

                ext = os.path.splitext(filename)[-1].lower()
                if ext == '.3mf':
                    base_path = os.path.join(homedir, "printer_data")
                    tempfile_target = os.path.join(".temp", os.path.basename(filename))
                    tempfile_dir = os.path.join(base_path, ".temp")
                    cache_path = os.path.join(base_path, ".cache")

                    os.makedirs(tempfile_dir, exist_ok=True)
                
                    for item in os.listdir(tempfile_dir):
                        item_path = os.path.join(tempfile_dir, item)
                        if os.path.isfile(item_path):
                            os.unlink(item_path)
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path)

                    _3mf_path = os.path.join(cache_path, os.path.basename(filename))
                    _MODEL_PATH = "Metadata/plate_"+ plateindex +".gcode"
                    file_base_name = os.path.splitext(os.path.basename(filename))[0]
                    if os.path.splitext(file_base_name)[-1] == '.gcode':
                        dest_path = os.path.splitext(os.path.basename(filename))[0]
                    else:
                        dest_path = os.path.splitext(os.path.basename(filename))[0] + ".gcode"
                    tempfile_target = os.path.join(".temp", os.path.basename(dest_path))

                    
                    try:
                        with zipfile.ZipFile(_3mf_path) as zf:
                            namelist = zf.namelist()
                            logging.info(f"namelist == {namelist}")
                            if _MODEL_PATH in namelist:
                                # gcode_content = zf.read(_MODEL_PATH)
                                temp_target_path = os.path.join(base_path, tempfile_target)
                                logging.info(f"开始写入gcode文件, filename = {filename}")
                                # await self.eventloop.run_in_thread(
                                #     self._write_file_content, temp_target_path, gcode_content
                                # )
                                with zf.open(_MODEL_PATH) as src_file, open(temp_target_path, 'wb') as dest_file:
                                    chunk_size = 10 * 1024 * 1024
                                    while True:
                                        chunk = src_file.read(chunk_size)
                                        if not chunk:
                                            break
                                        dest_file.write(chunk)

                                logging.info(f"gcode文件写入完成, filename = {filename}")
                    except Exception:
                        logging.info(traceback.format_exc())

                filename = os.path.split(filename)[1]

        # tempfile_path = os.path.join(base_path, filename)
        script = f'SDCARD_PRINT_FILE FILENAME="{filename}" PLATEINDEX="{plateindex}" WITHCHECK="{with_check}"'
        if wait_klippy_started:
            await self.klippy.wait_started()
        logging.info(f"Requesting Job Start, filename = {filename}")
        ret = await self.run_gcode(script)
        self.server.send_event("klippy_apis:job_start_complete", user)
        return ret

    def _write_file_content(self, file_path, content):
        stat = os.statvfs("/")
        free_space = stat.f_frsize * stat.f_bfree
        content_size = len(content)
        if content_size >= free_space:
            msg = "!! 磁盘空间不足，无法写入文件。"
            self.server.send_event("server:gcode_response", msg)
            raise self.server.error("磁盘空间不足，无法写入文件。", 500)
        
        with open(file_path, 'wb') as f:
            f.write(content)

    async def pause_print(
        self, default: Union[Sentinel, _T] = Sentinel.MISSING
    ) -> Union[_T, str]:
        self.server.send_event("klippy_apis:pause_requested")
        logging.info("Requesting job pause...")
        return await self._send_klippy_request(
            "pause_resume/pause", {}, default)

    async def resume_print(
        self, default: Union[Sentinel, _T] = Sentinel.MISSING
    ) -> Union[_T, str]:
        self.server.send_event("klippy_apis:resume_requested")
        logging.info("Requesting job resume...")
        return await self._send_klippy_request(
            "pause_resume/resume", {}, default)

    async def cancel_print(
        self, default: Union[Sentinel, _T] = Sentinel.MISSING
    ) -> Union[_T, str]:
        self.server.send_event("klippy_apis:cancel_requested")
        logging.info("Requesting job cancel...")
        return await self._send_klippy_request(
            "pause_resume/cancel", {}, default)

    async def breakheater(
        self, default: Union[Sentinel, _T] = Sentinel.MISSING
    ) -> Union[_T, str]:
        return await self._send_klippy_request(
            "breakheater", {}, default)

    async def breakmacro(
        self, default: Union[Sentinel, _T] = Sentinel.MISSING
    ) -> Union[_T, str]:
        return await self._send_klippy_request(
            "breakmacro", {}, default)

    async def do_restart(
        self, gc: str, wait_klippy_started: bool = False
    ) -> str:
        # WARNING: Do not call this method from within the following
        # event handlers when "wait_klippy_started" is set to True:
        # klippy_identified, klippy_started, klippy_ready, klippy_disconnect
        # Doing so will result in "wait_started" blocking for the specifed
        # timeout (default 20s) and returning False.
        if wait_klippy_started:
            await self.klippy.wait_started()
        try:
            result = await self.run_gcode(gc)
        except self.server.error as e:
            if str(e) == "Klippy Disconnected":
                result = "ok"
            else:
                raise
        return result

    async def list_endpoints(self,
                             default: Union[Sentinel, _T] = Sentinel.MISSING
                             ) -> Union[_T, Dict[str, List[str]]]:
        return await self._send_klippy_request(
            LIST_EPS_ENDPOINT, {}, default)

    async def emergency_stop(self) -> str:
        return await self._send_klippy_request(ESTOP_ENDPOINT, {})

    async def get_klippy_info(self,
                              send_id: bool = False,
                              default: Union[Sentinel, _T] = Sentinel.MISSING
                              ) -> Union[_T, Dict[str, Any]]:
        params = {}
        if send_id:
            ver = self.version
            params = {'client_info': {'program': "Moonraker", 'version': ver}}
        return await self._send_klippy_request(INFO_ENDPOINT, params, default)

    async def get_object_list(self,
                              default: Union[Sentinel, _T] = Sentinel.MISSING
                              ) -> Union[_T, List[str]]:
        result = await self._send_klippy_request(
            OBJ_LIST_ENDPOINT, {}, default)
        if isinstance(result, dict) and 'objects' in result:
            return result['objects']
        if default is not Sentinel.MISSING:
            return default
        raise self.server.error("Invalid response received from Klippy", 500)

    async def query_objects(self,
                            objects: Mapping[str, Optional[List[str]]],
                            default: Union[Sentinel, _T] = Sentinel.MISSING
                            ) -> Union[_T, Dict[str, Any]]:
        params = {'objects': objects}
        result = await self._send_klippy_request(
            STATUS_ENDPOINT, params, default)
        if isinstance(result, dict) and "status" in result:
            return result["status"]
        if default is not Sentinel.MISSING:
            return default
        raise self.server.error("Invalid response received from Klippy", 500)

    async def subscribe_objects(
        self,
        objects: Mapping[str, Optional[List[str]]],
        callback: Optional[SubCallback] = None,
        default: Union[Sentinel, _T] = Sentinel.MISSING
    ) -> Union[_T, Dict[str, Any]]:
        # The host transport shares subscriptions amongst all components
        for obj, items in objects.items():
            if obj in self.host_subscription:
                prev = self.host_subscription[obj]
                if items is None or prev is None:
                    self.host_subscription[obj] = None
                else:
                    uitems = list(set(prev) | set(items))
                    self.host_subscription[obj] = uitems
            else:
                self.host_subscription[obj] = items
        params = {"objects": dict(self.host_subscription)}
        result = await self._send_klippy_request(SUBSCRIPTION_ENDPOINT, params, default)
        if isinstance(result, dict) and "status" in result:
            if callback is not None:
                self.subscription_callbacks.append(callback)
            return result["status"]
        if default is not Sentinel.MISSING:
            return default
        raise self.server.error("Invalid response received from Klippy", 500)

    async def subscribe_from_transport(
        self,
        objects: Mapping[str, Optional[List[str]]],
        transport: APITransport,
        default: Union[Sentinel, _T] = Sentinel.MISSING,
    ) -> Union[_T, Dict[str, Any]]:
        params = {"objects": dict(objects)}
        result = await self._send_klippy_request(
            SUBSCRIPTION_ENDPOINT, params, default, transport
        )
        if isinstance(result, dict) and "status" in result:
            return result["status"]
        if default is not Sentinel.MISSING:
            return default
        raise self.server.error("Invalid response received from Klippy", 500)

    async def subscribe_gcode_output(self) -> str:
        template = {'response_template':
                    {'method': "process_gcode_response"}}
        return await self._send_klippy_request(GC_OUTPUT_ENDPOINT, template)

    async def register_method(self, method_name: str) -> str:
        return await self._send_klippy_request(
            REG_METHOD_ENDPOINT,
            {'response_template': {"method": method_name},
             'remote_method': method_name})

    def send_status(
        self, status: Dict[str, Any], eventtime: float
    ) -> None:
        for cb in self.subscription_callbacks:
            self.eventloop.register_callback(cb, status, eventtime)
        self.server.send_event("server:status_update", status)

def load_component(config: ConfigHelper) -> KlippyAPI:
    return KlippyAPI(config)
