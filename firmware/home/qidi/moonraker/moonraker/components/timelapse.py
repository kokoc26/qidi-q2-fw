# Moonraker Timelapse component
#
# Copyright (C) 2021 Christoph Frei <fryakatkop@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import logging
import os
import glob
import re
import shutil
import asyncio
from datetime import datetime
from tornado.ioloop import IOLoop
from zipfile import ZipFile

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Dict,
    Any
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from .webcam import WebcamManager, WebCam
    from websockets import WebRequest
    from . import shell_command
    from . import klippy_apis
    from . import database

    APIComp = klippy_apis.KlippyAPI
    SCMDComp = shell_command.ShellCommandFactory
    DBComp = database.MoonrakerDatabase


class Timelapse:

    def __init__(self, confighelper: ConfigHelper) -> None:

        # setup vars
        self.renderisrunning = False
        self.saveisrunning = False
        self.takingframe = False
        self.framecount = 0
        self.lastframefile = ""
        self.lastrenderprogress = 0
        self.lastcmdreponse = ""
        self.byrendermacro = False
        self.hyperlapserunning = False
        self.printing = False
        self.noWebcamDb = False

        self.confighelper = confighelper
        self.server = confighelper.get_server()
        self.klippy_apis: APIComp = self.server.lookup_component('klippy_apis')
        self.database: DBComp = self.server.lookup_component("database")

        # setup static (nonDB) settings
        out_dir_cfg = confighelper.get(
            "output_path", "~/timelapse/")
        temp_dir_cfg = confighelper.get(
            "frame_path", "/tmp/timelapse/")
        self.ffmpeg_binary_path = confighelper.get(
            "ffmpeg_binary_path", "/usr/bin/ffmpeg")
        self.wget_skip_cert = confighelper.getboolean(
            "wget_skip_cert_check", False)

        # Setup default config
        self.config: Dict[str, Any] = {
            'enabled': True,
            'mode': "layermacro",
            'camera': "",
            'snapshoturl': "http://localhost:8080/?action=snapshot",
            'stream_delay_compensation': 0.05,
            'gcode_verbose': False,
            'parkhead': False,
            'parkpos': "back_left",
            'park_custom_pos_x': 10.0,
            'park_custom_pos_y': 10.0,
            'park_custom_pos_dz': 0.0,
            'park_travel_speed': 100,
            'park_retract_speed': 15,
            'park_extrude_speed': 15,
            'park_retract_distance': 1.0,
            'park_extrude_distance': 1.0,
            'park_time': 0.1,
            'fw_retract': False,
            'hyperlapse_cycle': 30,
            'autorender': True,
            'constant_rate_factor': 23,
            'output_framerate': 30,
            'pixelformat': "yuv420p",
            'time_format_code': "%Y%m%d_%H%M",
            'extraoutputparams': "",
            'variable_fps': False,
            'targetlength': 10,
            'variable_fps_min': 5,
            'variable_fps_max': 60,
            'rotation': 0,
            'flip_x': False,
            'flip_y': False,
            'duplicatelastframe': 5,
            'previewimage': True,
            'saveframes': False
        }

        # Get Config from Database and overwrite defaults
        dbconfig: Dict[str, Any] = self.database.get_item("timelapse",
                                                          "config",
                                                          self.config)
        if isinstance(dbconfig, asyncio.Future):
            self.config.update(dbconfig.result())
        else:
            self.config.update(dbconfig)

        # Overwrite Config with fixed config made in moonraker.conf
        # this is a fallback to older setups and when the Frontend doesn't
        # support the settings endpoint
        self.overwriteDbconfigWithConfighelper()

        # check if ffmpeg is installed
        self.ffmpeg_installed = os.path.isfile(self.ffmpeg_binary_path)
        if not self.ffmpeg_installed:
            self.config['autorender'] = False
            logging.info(f"timelapse: {self.ffmpeg_binary_path} \
                        not found please install to use render functionality")

        # setup directories
        # remove trailing "/"
        out_dir_cfg = os.path.join(out_dir_cfg, '')
        temp_dir_cfg = os.path.join(temp_dir_cfg, '')
        # evaluate and expand "~"
        self.out_dir = os.path.expanduser(out_dir_cfg)
        self.temp_dir = os.path.expanduser(temp_dir_cfg)
        # create directories if they doesn't exist
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)

        # setup eventhandlers and endpoints
        file_manager = self.server.lookup_component("file_manager")
        file_manager.register_directory("timelapse",
                                        self.out_dir,
                                        full_access=True
                                        )
        file_manager.register_directory("timelapse_frames", self.temp_dir)
        self.server.register_notification("timelapse:timelapse_event")
        # self.server.register_event_handler(
        #     "server:gcode_response", self.handle_gcode_response)
        # self.server.register_event_handler(
        #     "server:status_update", self.handle_status_update)
        self.server.register_event_handler(
            "server:klippy_ready", self.handle_klippy_ready)
        self.server.register_remote_method(
            "timelapse_newframe", self.call_newframe)
        self.server.register_remote_method(
            "timelapse_saveFrames", self.call_saveFramesZip)
        self.server.register_remote_method(
            "timelapse_render", self.call_render)
        self.server.register_endpoint(
            "/machine/timelapse/render", ['POST'], self.render)
        self.server.register_endpoint(
            "/machine/timelapse/saveframes", ['POST'], self.saveFramesZip)
        self.server.register_endpoint(
            "/machine/timelapse/settings", ['GET', 'POST'],
            self.webrequest_settings)
        self.server.register_endpoint(
            "/machine/timelapse/lastframeinfo", ['GET'],
            self.webrequest_lastframeinfo)
        
        self.server.register_event_handler(
            "history:history_changed", self._on_history_changed)
        self.server.register_event_handler("server:klippy_ready", self._sync_on_ready)

    async def component_init(self) -> None:
        await self.getWebcamConfig()

    def overwriteDbconfigWithConfighelper(self) -> None:
        blockedsettings = []

        for config in self.confighelper.get_options():
            if config in self.config:
                configtype = type(self.config[config])
                if configtype == str:
                    self.config[config] = self.confighelper.get(config)
                elif configtype == bool:
                    self.config[config] = self.confighelper.getboolean(config)
                elif configtype == int:
                    self.config[config] = self.confighelper.getint(config)
                elif configtype == float:
                    self.config[config] = self.confighelper.getfloat(config)

                # add the config to list of blockedsettings
                blockedsettings.append(config)

        # append the list of blockedsettings to the config dict
        self.config.update({'blockedsettings': blockedsettings})
        logging.debug(f"blockedsettings {self.config['blockedsettings']}")

    async def getWebcamConfig(self) -> None:
        # Read Webcam config from Database
        webcam_name = self.config['camera']
        try:
            wcmgr: WebcamManager = self.server.lookup_component("webcam")
            cams = wcmgr.get_webcams()

            if not cams:
                logging.info("WARNING: no camera configured, " +
                             "using the fallback config")
                fallback = {'snapshot_url': self.config['snapshoturl'],
                            'rotation': self.config['rotation'],
                            'flip_horizontal': self.config['flip_x'],
                            'flip_vertical': self.config['flip_y']
                            }
                self.parseWebcamConfig(fallback)
                return

            if webcam_name and webcam_name in cams:
                camera = cams[webcam_name]
            else:
                camera = list(cams.values())[0]

            self.parseWebcamConfig(camera.as_dict())

        except Exception as e:
            logging.info(f"something went wrong getting"
                         f"Cam Camera:{webcam_name} from Database. "
                         f"Exception: {e}"
                         )

    def parseWebcamConfig(self, webcamconfig) -> None:
        snapshoturl = webcamconfig['snapshot_url']
        flip_x = webcamconfig['flip_horizontal']
        flip_y = webcamconfig['flip_vertical']
        rotation = webcamconfig['rotation']

        oldWebcamConfig = {"url": self.config['snapshoturl'],
                           "flip_x": self.config['flip_x'],
                           "flip_y": self.config['flip_y'],
                           "rotation": self.config['rotation']
                           }

        self.config['snapshoturl'] = self.confighelper.get('snapshoturl',
                                                           snapshoturl
                                                           )
        self.config['flip_x'] = self.confighelper.getboolean('flip_x',
                                                             flip_x
                                                             )
        self.config['flip_y'] = self.confighelper.getboolean('flip_y',
                                                             flip_y
                                                             )
        self.config['rotation'] = self.confighelper.getint('rotation',
                                                           rotation
                                                           )

        if not self.config['snapshoturl'].startswith('http'):
            if not self.config['snapshoturl'].startswith('/'):
                self.config['snapshoturl'] = "http://localhost/" + \
                                             self.config['snapshoturl']
            else:
                self.config['snapshoturl'] = "http://localhost" + \
                                             self.config['snapshoturl']

        # check if settings have changed and if so creat log entry
        newWebcamConfig = {"url": self.config['snapshoturl'],
                           "flip_x": self.config['flip_x'],
                           "flip_y": self.config['flip_y'],
                           "rotation": self.config['rotation']
                           }

        if not oldWebcamConfig == newWebcamConfig:
            logging.info("snapshoturl: "
                         f"{self.config['snapshoturl']}, "
                         f"Flip V/H: {self.config['flip_y']}/"
                         f"{self.config['flip_y']}, "
                         f"rotation: {self.config['rotation']}"
                         )

    async def webrequest_lastframeinfo(self,
                                       webrequest: WebRequest
                                       ) -> Dict[str, Any]:
        return {
            'framecount': self.framecount,
            'lastframefile': self.lastframefile
        }

    async def webrequest_settings(self,
                                  webrequest: WebRequest
                                  ) -> Dict[str, Any]:
        action = webrequest.get_action()
        if action == 'POST':

            args = webrequest.get_args()
            logging.debug("webreq_args: " + str(args))

            gcodechange = False
            settingsWithGcodechange = [
                'enabled', 'parkhead',
                'parkpos', 'park_custom_pos_x',
                'park_custom_pos_y', 'park_custom_pos_dz',
                'park_travel_speed', 'park_retract_speed',
                'park_extrude_speed', 'park_retract_distance',
                'park_extrude_distance', 'park_time', 'fw_retract'
            ]
            modechanged = False

            for setting in args:
                if setting in self.config:
                    settingtype = type(self.config[setting])
                    if setting == "snapshoturl":
                        logging.debug(
                            "snapshoturl cannot be changed via webrequest")
                    elif settingtype == str:
                        settingvalue = webrequest.get(setting)
                    elif settingtype == bool:
                        settingvalue = webrequest.get_boolean(setting)
                    elif settingtype == int:
                        settingvalue = webrequest.get_int(setting)
                    elif settingtype == float:
                        settingvalue = webrequest.get_float(setting)

                    self.config[setting] = settingvalue

                    self.database.insert_item(
                        "timelapse",
                        f"config.{setting}",
                        settingvalue
                    )

                    if setting == "camera":
                        if not self.noWebcamDb:
                            await self.getWebcamConfig()
                        else:
                            logging.info("Webcam Namespace not intialized, "
                                         "please restart moonraker service!")

                    if setting in settingsWithGcodechange:
                        gcodechange = True

                    if setting == "mode":
                        modechanged = True
                    
                    if setting == "enabled":
                        ioloop = IOLoop.current()
                        ioloop.spawn_callback(self._sync_on_ready)

                    logging.debug(f"changed setting: {setting} "
                                  f"value: {settingvalue} "
                                  f"type: {settingtype}"
                                  )

            if modechanged:
                if self.config['mode'] == "hyperlapse":
                    if not self.hyperlapserunning:
                        if self.printing:
                            ioloop = IOLoop.current()
                            ioloop.spawn_callback(self.start_hyperlapse)
                else:
                    if self.hyperlapserunning:
                        ioloop = IOLoop.current()
                        ioloop.spawn_callback(self.stop_hyperlapse)
            if gcodechange:
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.setgcodevariables)

        return self.config

    async def handle_klippy_ready(self) -> None:
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.setgcodevariables)

        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.stop_hyperlapse)

    async def setgcodevariables(self) -> None:
        gcommand = "_SET_TIMELAPSE_SETUP " \
            + f" ENABLE={self.config['enabled']}" \
            + f" VERBOSE={self.config['gcode_verbose']}" \
            + f" PARK_ENABLE={self.config['parkhead']}" \
            + f" PARK_POS={self.config['parkpos']}" \
            + f" CUSTOM_POS_X={self.config['park_custom_pos_x']}" \
            + f" CUSTOM_POS_Y={self.config['park_custom_pos_y']}" \
            + f" CUSTOM_POS_DZ={self.config['park_custom_pos_dz']}" \
            + f" TRAVEL_SPEED={self.config['park_travel_speed']}" \
            + f" RETRACT_SPEED={self.config['park_retract_speed']}" \
            + f" EXTRUDE_SPEED={self.config['park_extrude_speed']}" \
            + f" RETRACT_DISTANCE={self.config['park_retract_distance']}" \
            + f" EXTRUDE_DISTANCE={self.config['park_extrude_distance']}" \
            + f" PARK_TIME={self.config['park_time']}" \
            + f" FW_RETRACT={self.config['fw_retract']}" \

        logging.debug(f"run gcommand: {gcommand}")
        try:
            await self.klippy_apis.run_gcode(gcommand)
        except self.server.error:
            msg = f"Error executing GCode {gcommand}"
            logging.exception(msg)

    def call_newframe(self, macropark=False, hyperlapse=False) -> None:
        if self.config['enabled']:
            if self.config['mode'] == "hyperlapse":
                if hyperlapse:
                    if not self.takingframe:
                        self.takingframe = True
                        self.spawn_newframe_callbacks()
                    else:
                        logging.info("last take frame hasn't completed"
                                     + " ignoring take frame command"
                                     )
                else:
                    logging.info("ignoring non hyperlapse triggered macros"
                                 + "in hyperlapse mode"
                                 )
            else:
                self.spawn_newframe_callbacks()
        else:
            logging.info("NEW_FRAME macro ignored timelapse is disabled")

    def spawn_newframe_callbacks(self) -> None:
        ioloop = IOLoop.current()
        # release parked head after park time is passed
        park_time = self.config['park_time']
        ioloop.call_later(delay=park_time, callback=self.release_parkedhead)
        # capture the frame after stream delay is passed
        stream_delay = self.config['stream_delay_compensation']
        ioloop.call_later(delay=stream_delay, callback=self.newframe)

    async def release_parkedhead(self) -> None:
        gcommand = "SET_GCODE_VARIABLE " \
            + "MACRO=TIMELAPSE_TAKE_FRAME " \
            + "VARIABLE=takingframe VALUE=False"

        logging.debug(f"run gcommand: {gcommand}")
        try:
            await self.klippy_apis.run_gcode(gcommand)
        except self.server.error:
            msg = f"Error executing GCode {gcommand}"
            logging.exception(msg)

    async def start_hyperlapse(self) -> None:
        hyperlapse_cycle = self.config['hyperlapse_cycle']
        park_time = self.config['park_time']
        timediff = hyperlapse_cycle - park_time
        if timediff >= 1:
            gcommand = "HYPERLAPSE ACTION=START" \
                       + f" CYCLE={hyperlapse_cycle}"

            logging.debug(f"run gcommand: {gcommand}")
            try:
                await self.klippy_apis.run_gcode(gcommand)
            except self.server.error:
                msg = f"Error executing GCode {gcommand}"
                logging.exception(msg)
            self.hyperlapserunning = True
        else:
            logging.info("WARNING: Blocked start of Hyperlapse, because "
                         f"hyperlapse_cycle ({hyperlapse_cycle}s) is smaller "
                         f"then or to close to park_time ({park_time}s)"
                         )

    async def stop_hyperlapse(self) -> None:
        gcommand = "HYPERLAPSE ACTION=STOP"

        logging.debug(f"run gcommand: {gcommand}")
        try:
            await self.klippy_apis.run_gcode(gcommand)
        except self.server.error:
            msg = f"Error executing GCode {gcommand}"
            logging.exception(msg)
        self.hyperlapserunning = False

    async def newframe(self) -> None:
        # make sure webcamconfig is uptodate before grabbing a new frame
        await self.getWebcamConfig()

        options = ""
        if self.wget_skip_cert:
            options += "--no-check-certificate "

        self.framecount += 1
        framefile = "frame" + str(self.framecount).zfill(6) + ".jpg"
        cmd = "wget " + options + self.config['snapshoturl'] \
              + " -O " + self.temp_dir + framefile
        self.lastframefile = framefile
        logging.debug(f"cmd: {cmd}")

        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        scmd = shell_cmd.build_shell_command(cmd, None)
        try:
            cmdstatus = await scmd.run(timeout=2., verbose=False)
        except Exception:
            logging.exception(f"Error running cmd '{cmd}'")

        result = {'action': 'newframe'}
        if cmdstatus:
            result.update({
                'frame': str(self.framecount),
                'framefile': framefile,
                'status': 'success'
            })
        else:
            logging.info(f"getting newframe failed: {cmd}")
            self.framecount -= 1
            result.update({'status': 'error'})

        self.notify_event(result)
        self.takingframe = False

    async def handle_status_update(self, status: Dict[str, Any]) -> None:
        if 'print_stats' in status:
            printstats = status['print_stats']
            if 'state' in printstats:
                state = printstats['state']
                if state == 'cancelled':
                    self.printing = False
                    ioloop = IOLoop.current()
                    ioloop.spawn_callback(self.stop_hyperlapse)

    async def handle_gcode_response(self, gresponse: str) -> None:
        if gresponse == "File selected":
            # print_started
            self.cleanup()
            self.printing = True

            # start hyperlapse if mode is set
            if self.config['mode'] == "hyperlapse":
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.start_hyperlapse)

        elif gresponse == "Done printing file":
            # print_done
            self.printing = False

            # stop hyperlapse if mode is set
            if self.config['mode'] == "hyperlapse":
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.stop_hyperlapse)

            if self.config['enabled']:
                if self.config['saveframes']:
                    ioloop = IOLoop.current()
                    ioloop.spawn_callback(self.saveFramesZip)
                if self.config['autorender']:
                    ioloop = IOLoop.current()
                    ioloop.spawn_callback(self.render)

    def cleanup(self) -> None:
        logging.debug("cleanup frame directory")
        filelist = glob.glob(self.temp_dir + "frame*.jpg")
        if filelist:
            for filepath in filelist:
                os.remove(filepath)
        self.framecount = 0
        self.lastframefile = ""

    def call_saveFramesZip(self) -> None:
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.saveFramesZip)

    async def saveFramesZip(self, webrequest=None):
        filelist = sorted(glob.glob(self.temp_dir + "frame*.jpg"))
        self.framecount = len(filelist)
        result = {'action': 'saveframes'}

        if not filelist:
            msg = "no frames to save, skip"
            status = "skipped"
        elif self.saveisrunning:
            msg = "saving frames already"
            status = "running"
        else:
            self.saveisrunning = True

            # get printed filename
            kresult = await self.klippy_apis.query_objects(
                {'print_stats': None})
            pstats = kresult.get("print_stats", {})
            gcodefilename = pstats.get("filename", "").split("/")[-1]

            # prepare output filename
            now = datetime.now()
            date_time = now.strftime(self.config['time_format_code'])
            outfile = f"timelapse_{gcodefilename}_{date_time}"
            outfileFull = outfile + "_frames.zip"

            zipObj = ZipFile(self.out_dir + outfileFull, "w")

            for frame in filelist:
                zipObj.write(frame, frame.split("/")[-1])

            logging.info(f"saved frames: {outfile}_frames.zip")

            result.update({
                'status': 'finished',
                'zipfile': outfileFull
            })

            self.saveisrunning = False

        return result

    def call_render(self, byrendermacro=False) -> None:
        self.byrendermacro = byrendermacro
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.render)

    async def render(self, webrequest=None, job_name=None):
        start_time = datetime.now()
        logging.info(f"--- [Render Start] --- Target: {job_name}")

        filelist = sorted(glob.glob(self.temp_dir + "frame*.jpg"))
        self.framecount = len(filelist)
        result = {'action': 'render'}

        # make sure webcamconfig is uptodate for the rotation/flip feature
        # 埋点 1: 检查摄像头配置获取耗时
        logging.info("Step 1: Fetching Webcam Config...")
        ts = datetime.now()
        await self.getWebcamConfig()
        logging.info(f"Step 1 Done. Cost: {(datetime.now() - ts).total_seconds()}s")

        if not filelist:
            msg = "no frames to render, skip"
            status = "skipped"
        elif self.renderisrunning:
            msg = "render is already running"
            status = "running"
        elif not self.ffmpeg_installed:
            msg = f"{self.ffmpeg_binary_path} not found, please install ffmpeg"
            status = "error"
            # cmd = outfile = None
            logging.info(f"timelapse: {msg}")
        else:
            self.renderisrunning = True

            # get printed filename
            # 埋点 2: 文件名处理
            if job_name:
                gcodefilename = job_name
            else:
                logging.info("Step 2: Querying Klippy for filename...")
                ts = datetime.now()
                kresult = await self.klippy_apis.query_objects({'print_stats': None})
                pstats = kresult.get("print_stats", {})
                gcodefilename = pstats.get("filename", "").split("/")[-1]
                logging.info(f"Step 2 Done. Cost: {(datetime.now() - ts).total_seconds()}s")

            # prepare output filename
            now = datetime.now()
            date_time = now.strftime(self.config['time_format_code'])
            inputfiles = self.temp_dir + "frame%6d.jpg"
            outfile = f"timelapse_{gcodefilename}_{date_time}"

            # dublicate last frame
            # 埋点 3: 最后一帧复制 (IO 操作)
            duplicates = []
            if self.config['duplicatelastframe'] > 0:
                logging.info(f"Step 3: Duplicating last frame ({self.config['duplicatelastframe']} times)...")
                ts = datetime.now()
                lastframe = filelist[-1:][0]
                for i in range(self.config['duplicatelastframe']):
                    nextframe = str(self.framecount + i + 1).zfill(6)
                    duplicatePath = self.temp_dir + "frame" + nextframe + ".jpg"
                    duplicates.append(duplicatePath)
                    try:
                        shutil.copy(lastframe, duplicatePath)
                    except OSError as err:
                        logging.info(f"Copy failed: {err}")
                filelist = sorted(glob.glob(self.temp_dir + "frame*.jpg"))
                self.framecount = len(filelist)
                logging.info(f"Step 3 Done. Cost: {(datetime.now() - ts).total_seconds()}s")

            # variable framerate
            if self.config['variable_fps']:
                fps = int(self.framecount / self.config['targetlength'])
                fps = max(min(fps,
                              self.config['variable_fps_max']),
                          self.config['variable_fps_min'])
            else:
                fps = self.config['output_framerate']

            # apply rotation
            filterParam = ""
            if self.config['rotation'] == 90 and self.config['flip_y']:
                filterParam = " -vf 'transpose=3'"
            elif self.config['rotation'] == 90:
                filterParam = " -vf 'transpose=1'"
            elif self.config['rotation'] == 180:
                filterParam = " -vf 'hflip,vflip'"
            elif self.config['rotation'] == 270:
                filterParam = " -vf 'transpose=2'"
            elif self.config['rotation'] == 270 and self.config['flip_y']:
                filterParam = " -vf 'transpose=0'"
            elif self.config['rotation'] > 0:
                pi = 3.141592653589793
                rot = str(self.config['rotation']*(pi/180))
                filterParam = " -vf 'rotate=" + rot + "'"
            elif self.config['flip_x'] and self.config['flip_y']:
                filterParam = " -vf 'hflip,vflip'"
            elif self.config['flip_x']:
                filterParam = " -vf 'hflip'"
            elif self.config['flip_y']:
                filterParam = " -vf 'vflip'"

            # build shell command
            # cmd = self.ffmpeg_binary_path \
            #     + " -r " + str(fps) \
            #     + " -i '" + inputfiles + "'" \
            #     + filterParam \
            #     + " -threads 2 -g 5" \
            #     + " -crf " + str(self.config['constant_rate_factor']) \
            #     + " -vcodec libx264" \
            #     + " -pix_fmt " + self.config['pixelformat'] \
            #     + " -an" \
            #     + " " + self.config['extraoutputparams'] \
            #     + " '" + self.temp_dir + outfile + ".mp4' -y"
            # build shell command (RK3308 4-Core Optimized) # 做性能适配，能够快速合并视频
            cmd = self.ffmpeg_binary_path \
                + " -r " + str(fps) \
                + " -i '" + inputfiles + "'" \
                + filterParam \
                + " -threads 0" \
                + " -preset ultrafast" \
                + " -tune stillimage" \
                + " -g 60" \
                + " -crf " + str(self.config['constant_rate_factor']) \
                + " -vcodec libx264" \
                + " -pix_fmt " + self.config['pixelformat'] \
                + " -an" \
                + " " + self.config['extraoutputparams'] \
                + " '" + self.temp_dir + outfile + ".mp4' -y"

            # log and notify ws
            logging.info(f"start FFMPEG: {cmd}")
            result.update({
                'status': 'started',
                'framecount': str(self.framecount),
                'settings': {
                    'framerate': fps,
                    'crf': self.config['constant_rate_factor'],
                    'pixelformat': self.config['pixelformat']
                }
            })

           # 埋点 4: FFMPEG 执行过程
            logging.info(f"Step 4: Starting FFMPEG Process...")
            logging.info(f"CMD: {cmd}")
            shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
            self.notify_event(result)
            scmd = shell_cmd.build_shell_command(cmd, self.ffmpeg_cb)
            
            ts = datetime.now()
            try:
                # 这里的 await 是最容易卡住的地方
                cmdstatus = await scmd.run(verbose=True, log_complete=False, timeout=9999)
                logging.info(f"Step 4 FFMPEG Finished. Status: {cmdstatus}. Cost: {(datetime.now() - ts).total_seconds()}s")
            except Exception:
                logging.exception("Step 4 Error")

            # check success
            if cmdstatus:
                status = "success"
                msg = f"Rendering Video successful: {outfile}.mp4"
                result.update({
                    'filename': f"{outfile}.mp4",
                    'printfile': gcodefilename
                })
                # result.pop("framecount")
                result.pop("settings")

                # move finished output file to output directory
                logging.info(f"Step 5: Moving file to {self.out_dir}...")
                ts = datetime.now()
                try:
                    shutil.move(self.temp_dir + outfile + ".mp4", self.out_dir + outfile + ".mp4")
                    logging.info(f"Step 5 Done. Cost: {(datetime.now() - ts).total_seconds()}s")
                except OSError as err:
                    logging.info(f"Move failed: {err}")

                # copy image preview
                if self.config['previewimage']:
                    logging.info("Step 6: Processing Preview Image...")
                    ts = datetime.now()
                    previewFile = f"{outfile}.jpg"
                    previewFilePath = self.out_dir + previewFile
                    previewSrc = filelist[-1:][0]
                    try:
                        shutil.copy(previewSrc, previewFilePath)
                    except OSError as err:
                        logging.info(f"copying preview image failed: {err}")
                    else:
                        result.update({
                            'previewimage': previewFile
                        })

                    # apply rotation previewimage if needed
                    if filterParam or self.config['extraoutputparams']:
                        cmd = self.ffmpeg_binary_path \
                            + " -i '" + previewFilePath + "'" \
                            + filterParam \
                            + " -an" \
                            + " " + self.config['extraoutputparams'] \
                            + " '" + previewFilePath + "' -y"

                        logging.info(f"Rotate preview image cmd: {cmd}")

                        scmd = shell_cmd.build_shell_command(cmd)
                        try:
                            cmdstatus = await scmd.run(verbose=True,
                                                       log_complete=False,
                                                       timeout=9999999999,
                                                       )
                        except Exception:
                            logging.exception(f"Error running cmd '{cmd}'")
                    logging.info(f"Step 6 Done. Cost: {(datetime.now() - ts).total_seconds()}s")

            else:
                status = "error"
                msg = f"Rendering Video failed: {cmd} : {self.lastcmdreponse}"
                result.update({
                    'cmd': cmd,
                    'cmdresponse': self.lastcmdreponse
                })

            self.renderisrunning = False

            # cleanup duplicates
            if duplicates:
                ts = datetime.now()
                for dupe in duplicates:
                    try: os.remove(dupe)
                    except: pass
                logging.info(f"Step 7 Cleanup Done. Cost: {(datetime.now() - ts).total_seconds()}s")
        # log and notify ws
        logging.info(msg)
        result.update({
            'status': status,
            'msg': msg
        })
        self.notify_event(result)

        # confirm render finish to stop the render macro loop
        if self.byrendermacro:
            gcommand = "SET_GCODE_VARIABLE " \
                       + "MACRO=TIMELAPSE_RENDER VARIABLE=render VALUE=False"
            logging.debug(f"run gcommand: {gcommand}")
            try:
                await self.klippy_apis.run_gcode(gcommand)
            except self.server.error:
                msg = f"Error executing GCode {gcommand}"
                logging.exception(msg)
            self.byrendermacro = False
        total_cost = (datetime.now() - start_time).total_seconds()
        logging.info(f"--- [Render Finished] --- Total Time: {total_cost}s. Status: {status}")
        return result

    def ffmpeg_cb(self, response):
        # logging.debug(f"ffmpeg_cb: {response}")
        self.lastcmdreponse = response.decode("utf-8")
        try:
            frame = re.search(
                r'(?<=frame=)*(\d+)(?=.+fps)', self.lastcmdreponse
            ).group()
        except AttributeError:
            return
        percent = int(frame) / self.framecount * 100
        if percent > 100:
            percent = 100

        if self.lastrenderprogress != int(percent):
            self.lastrenderprogress = int(percent)
            # logging.debug(f"ffmpeg Progress: {self.lastrenderprogress}% ")
            result = {
                'action': 'render',
                'status': 'running',
                'progress': self.lastrenderprogress
            }
            self.notify_event(result)

    def notify_event(self, result: Dict[str, Any]) -> None:
        logging.debug(f"notify_event: {result}")
        self.server.send_event("timelapse:timelapse_event", result)
    
    def get_status(self, eventtime=None):
        return {
            'enabled': self.config['enabled']
        }
    
    async def _on_history_changed(self, data: Dict[str, Any]) -> None:
        """
        利用 History 模块的事件统一管理延时摄影生命周期
        data 结构参考: {'action': 'finished', 'job': {...}}
        """
        action = data.get('action')
        job_data = data.get('job', {})
        
        # 获取文件名 (History 已经帮我们格式化好了)
        raw_path = job_data.get('filename', 'unknown_job')
        job_name = raw_path.split('/')[-1]
        
        # 获取任务最终状态 (completed, cancelled, error, etc.)
        status = job_data.get('status')

        # --- 场景 1: 任务开始 (action: added) ---
        if action == "added":
            logging.info(f"Timelapse: [History Added] {job_name}. Cleaning up old frames.")
            self.cleanup() # 清理上一份残余图片
            self.printing = True
            
            if self.config.get('mode') == "hyperlapse":
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.start_hyperlapse)

        # --- 场景 2: 任务结束 (action: finished) ---
        elif action == "finished":
            # 过滤掉不需要渲染的特殊结束状态
            if status == "server_exit":
                logging.info("Timelapse: Server exit detected, skipping render.")
                self.printing = False
                return

            logging.info(f"Timelapse: [History Finished] {job_name} with status: {status}")
            
            # 1. 针对非正常完成的状态，尝试补拍一张故障现场图
            if status in ("error", "cancelled", "klippy_shutdown", "klippy_disconnect"):
                logging.warning(f"Timelapse: Abnormal end ({status}). Taking emergency snapshot.")
                await self._emergency_snapshot()

            # 2. 执行最终合并与清理逻辑
            await self._finalize_timelapse(job_name)

    async def _emergency_snapshot(self):
        """异常中断时的补拍逻辑"""
        try:
            # 调用你原有的拍照函数
            self.call_newframe()
            # 给 IO 一点缓冲时间确保图片写入磁盘
            await asyncio.sleep(1.0)
        except Exception as e:
            logging.warning(f"Timelapse: Emergency snapshot failed: {e}")

    async def _finalize_timelapse(self, job_name: str):
        """收尾：停止拍摄循环、保存、并触发 FFmpeg 合成"""
        self.printing = False
        
        # --- RK3308 核心优化：错峰延迟 ---
        # History 模块刚写完数据库，此时磁盘 IO 压力正处于峰值。
        # 等待 5 秒避开洪峰，防止 FFmpeg 合成初期的图片读取被卡住。
        await asyncio.sleep(5.0)

        if self.config.get('mode') == "hyperlapse":
            ioloop = IOLoop.current()
            ioloop.spawn_callback(self.stop_hyperlapse)

        if self.config.get('enabled'):
            # 逻辑 A: 保存原始帧 Zip (如果配置开启)
            if self.config.get('saveframes'):
                logging.info(f"Timelapse: Saving frames to zip for {job_name}")
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.saveFramesZip)
            
            # 逻辑 B: 自动渲染视频
            if self.config.get('autorender'):
                logging.info(f"Timelapse: Starting render process for {job_name}")
                # 依然使用 spawn_callback 异步执行 render，不阻塞主线程
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.render, job_name=job_name)

    async def _sync_on_ready(self):
        try:
            is_enabled_raw = self.config.get('enabled', True)
            is_enabled = 1 if is_enabled_raw else 0

            params = []
            params.append(f"TIMELAPSE={is_enabled}")
            sync_cmd = f"SMART_STATUS {' '.join(params)}"
            await self.klippy_apis.run_gcode(sync_cmd)
            logging.info(f"Syncing Timelapse and LEDs: {sync_cmd}")

        except Exception as e:
            logging.error(f"Failed to sync status on ready: {str(e)}")
    



def load_component(config: ConfigHelper) -> Timelapse:
    return Timelapse(config)
