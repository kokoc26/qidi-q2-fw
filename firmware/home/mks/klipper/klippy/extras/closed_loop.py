import logging


class ClosedLoopCurrentHelper:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = stepper_name = config.get_name().split()[-1]

        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()

        # a_pin = config.get('a_pin')
        # b_pin = config.get('b_pin')

        self.tx_func = self.rx_func = 0x00      # 发送的功能函数

        self.current_msg = None

        self.msg_index = 0

        self.addr = config.getint('addr')

        ppins = self.printer.lookup_object('pins')
        mcu_name = config.get('mcu')
        self.mcu = ppins.chips[mcu_name]

        self.oid = self.mcu.create_oid()

        self.mcu.register_config_callback(self._build_config)

        self.mcu.register_response(self._handle_data, "cl_response", self.oid)

        self.mcu.register_response(self._handle_rx_data, "cl_idx_rx")

        max_cur = 3000
        
        # 设置工作电流
        self.run_current = config.getint('run_current', maxval=max_cur)
        # 保持工作电流
        self.hold_current = config.getint('hold_current', maxval=max_cur)
        # 设置归零电流
        self.home_current = config.getint('home_current', maxval=max_cur)

        self.cl_send_cmd = None

        self.set_operating_current_cmd = None
        self.set_homing_current_cmd = None
        self.set_homing_trigger_current_cmd = None
        self.set_homing_state_cmd = None
        self.set_pulse_delay = None
        self.set_the_stall_tolerance_cmd = None
        self.set_the_slave_address_cmd = None

        # self.msg_set_run_current_data = self.msg_set_run_current_data
        self.msg_set_operating_current_data = self.msg_set_current(self.home_current)
        # 默认归零电流
        self.msg_set_default_homing_current_data = self.msg_set_homing_current(self.home_current)
        # self.msg_set_default_homing_trigger_current_data = self.msg_set_homing_state(2)


        # self.gcode.register_command("TEST_SEND_DATA", self.cmd_TEST_SEND_DATA, 
        #                             desc=self.cmd_TEST_SEND_DATA_help)

        self.gcode.register_mux_command("TEST_READ_DATA", "STEPPER", stepper_name, 
                                        self.cmd_TEST_READ_DATA, desc=self.cmd_TEST_READ_DATA_help)

        # self.gcode.register_command("TEST_READ_DATA", self.cmd_TEST_READ_DATA, 
        #                             desc=self.cmd_TEST_READ_DATA_help)

        self.gcode.register_mux_command("SET_OPERATING_CURRENT", "STEPPER", stepper_name,
                                        self.cmd_SET_OPERATING_CURRENT, desc=self.cmd_SET_OPERATING_CURRENT_help)

        self.gcode.register_mux_command("SET_HOMING_MODE", "STEPPER", stepper_name,
                                        self.cmd_SET_HOMING_MODE, desc=self.cmd_SET_HOMING_MODE_help)

        # self.gcode.register_command("SET_DEFAULT_HOMING_CURRENT", self.cmd_SET_DEFAULT_HOMING_CURRENT,
        #                             desc=self.cmd_SET_DEFAULT_HOMING_CURRENT_help)
        
        self.gcode.register_mux_command("SET_HOMING_CURRENT", "STEPPER", stepper_name,
                                        self.cmd_SET_HOMING_CURRENT, desc=self.cmd_SET_HOMING_CURRENT_help)
        

        self.gcode.register_mux_command("SET_HOMING_STATE", "STEPPER", stepper_name,
                                    self.cmd_SET_HOMING_STATE, 
                                    desc=self.cmd_SET_HOMING_STATE_help)

        self.gcode.register_mux_command("SET_HOMING_TRIGGER_CURRENT", "STEPPER", stepper_name,
                                    self.cmd_SET_HOMING_TRIGGER_CURRENT, 
                                    desc=self.cmd_SET_HOMING_TRIGGER_CURRENT_help)
        

    def _build_config(self):
        cmdqueue = self.mcu.alloc_command_queue()

        self.mcu.add_config_cmd("config_clmotor oid=%d addr=%u"
                        % (self.oid, self.addr))

        # self.msg_set_default_homing_current_data
        # self.cl_send_cmd = self.mcu.lookup_query_command(
        #     "cl_send oid=%c write=%*s",
        #     "cl_response oid=%c data=%*s", oid=self.oid,
        #     cq=cmdqueue, is_async=True
        # )
        # self.closedloop_send_cmd = self.mcu.lookup_query_command(
        #     "cl_send oid=%c write=%*s"
        # )

        self.cl_send_cmd = self.mcu.lookup_command(
            "cl_send oid=%c write=%*s", cq=cmdqueue)

        self.cl_rece_cmd = self.mcu.lookup_command(
            "cl_receive oid=%c", cq=cmdqueue)

        # self.mcu.add_config_cmd("cl_send oid=%c write=%*s"
        #                 % (self.oid, 6, self.msg_set_default_homing_current_data))

    cmd_SET_HOMING_MODE_help = "电机归零模式"
    def cmd_SET_HOMING_MODE(self, gcmd):
        val = gcmd.get_int('VALUE')
        msg = self.msg_set_homing_mode(val)
        self._send_data(msg)

    cmd_SET_OPERATING_CURRENT_help = "电机工作电流"
    def cmd_SET_OPERATING_CURRENT(self, gcmd):
        val = gcmd.get_int('VALUE')
        msg = self.msg_set_current(val)
        self._send_data(msg)

    cmd_TEST_READ_DATA_help = "Read rx data!"
    def cmd_TEST_READ_DATA(self, gcmd):
        self.cl_rece_cmd.send([self.oid])

    cmd_TEST_SEND_DATA_help = "TEST send data!"
    def cmd_TEST_SEND_DATA(self, gcmd):
        # msg = bytearray([0xfa, 0x00, 0x93, 0x02, 0x58, 0xe7])
        msg = bytearray([0xff, 0x55, 0xff, 0x00, 0x00, 0x00])
        self._send_data(msg)
    
    cmd_SET_DEFAULT_HOMING_CURRENT_help = "Reset homing current!"
    def cmd_SET_DEFAULT_HOMING_CURRENT(self, gcmd):
        msg = self.msg_set_homing_current(self.home_current)
        self._send_data(msg)

    cmd_SET_HOMING_CURRENT_help = "归零电流设置"
    def cmd_SET_HOMING_CURRENT(self, gcmd):
        val = gcmd.get_int('VALUE')
        msg = self.msg_set_homing_current(val)
        self._send_data(msg)


    cmd_SET_HOMING_STATE_help = "Set homing state! 1 电机进入归零状态 2 电机退出归零模式"
    def cmd_SET_HOMING_STATE(self, gcmd):
        val = gcmd.get_int('VALUE')
        msg = self.msg_set_homing_state(val)
        # 1 电机进入归零状态 2 电机退出归零模式
        self._send_data(msg)

    cmd_SET_HOMING_TRIGGER_CURRENT_help = "设置归零触发参数"
    def cmd_SET_HOMING_TRIGGER_CURRENT(self, gcmd):
        sval = gcmd.get_int('SV')
        eval = gcmd.get_int('EV')
        msg = self.msg_set_homing_trigger_current(sval, eval)
        # 1 电机进入归零状态 2 电机退出归零模式
        self._send_data(msg)

    def _handle_rx_data(self, params):
        self.rx_func = params['rx_data'][2]
        logging.info(f"rx_data = {params['rx_data']}")
        logging.info(f"\033[32m rx_func == {self.rx_func} \033[0m")
        logging.info(f"\033[32m tx_func == {self.tx_func} \033[0m")
        if self.rx_func == self.tx_func:
            if params['rx_data'][3] == 1:
                logging.info(f"设置成功\n")
            else:
                logging.info(f"设置失败，再次发送\n")
                self.cl_send_cmd.send([self.oid, self.current_msg])

    def _handle_data(self, params):
        logging.info(f"handle data = {params}")

    def _serial_485_write(self, data, minclock=0, reqclock=0):
        if self.cl_send_cmd is None:
            data_msg = "".join(["%02x" % (x,) for x in data])
            self.mcu.add_config_cmd("cl_send oid=%c write=%s" % (
                self.oid, data_msg), is_init=True
            )
            return
        self.cl_send_cmd.send([self.oid, data],minclock=minclock, reqclock=reqclock)

    def _serial_485_write_wait_ack(self, data, minclock=0, reqclock=0):
        self.cl_send_cmd.send_wait_ack([self.oid, data], minclock=minclock, reqclock=reqclock)

    def _send_data(self, msg):
        self.current_msg = msg
        self.tx_func = msg[2]
        self.reactor.pause(self.reactor.monotonic() + .008)
        self.cl_send_cmd.send([self.oid, msg])
        self.reactor.pause(self.reactor.monotonic() + .01)

        # params = self.cl_send_cmd.send([self.oid, msg], reqclock=0x7fffffff00000000)
        # logging.info(f"params = {params}")
        # toolhead = self.printer.lookup_object('toolhead')
        # toolhead.dwell(1.)

        # params = self.cl_rece_cmd.send([self.oid])
        # logging.info(f"params = {params}")
        # if params['data'][4] == 0x01:
        #     self.gcode.respond_raw("闭环电机配置成功")

        # if params['data'][3] == msg[3] and params['data'][4] == 0x01:
        #     self.gcode.respond_raw("闭环电机配置成功")

    def _calc_crc8(self, data):
        checksum = sum(data)
        return checksum % 256

    def _encode_write(self, sync, addr, fun, val):
        byte_val = val.to_bytes(2, byteorder='big')
        data = [sync, addr, fun] + list(byte_val)
        crc_val = self._calc_crc8(data)
        logging.info(f"data = {data}\n crc_val = {crc_val}")
        msg = bytearray(data) + bytearray([crc_val])
        logging.info(" ".join(f"{x:02x}" for x in data))
        return msg

    # 两个字节参数的编码
    def _encode_write_2(self, sync, addr, fun, val):
        byte_val = val.to_bytes(2, byteorder='big')
        data = [sync, addr, fun] + list(byte_val)
        crc_val = self._calc_crc8(data)
        logging.info(f"data = {data}\n crc_val = {crc_val}")
        msg = bytearray(data) + bytearray([crc_val])
        logging.info(" ".join(f"{x:02x}" for x in data))
        return msg

    # 一个字节参数的编码
    def _encode_write_1(self, sync, addr, fun, val):
        byte_val = val.to_bytes(1, byteorder='big')
        data = [sync, addr, fun] + list(byte_val)
        crc_val = self._calc_crc8(data)
        logging.info(f"data = {data}\n crc_val = {crc_val}")
        msg = bytearray(data) + bytearray([crc_val])
        logging.info(" ".join(f"{x:02x}" for x in data))
        return msg

    def _encode_trigger_data(self, sync, addr, fun, startval, endval):
        start_byte_val = startval.to_bytes(2, byteorder='big')
        end_byte_val = endval.to_bytes(1, byteorder='big')
        data = [sync, addr, fun] + list(start_byte_val) + list(end_byte_val)
        crc_val = self._calc_crc8(data)
        logging.info(f"data = {data}\n crc_val = {crc_val}")
        msg = bytearray(data) + bytearray([crc_val])
        logging.info(" ".join(f"{x:02x}" for x in data))
        return msg

    
    # 接收解析返回来的消息
    def _decode_read(self, data):
        pass

    # 设置工作电流
    def msg_set_current(self, val):
        return self._encode_write(0xfa, self.addr, 0x83, val)

    # 设置归零电流
    def msg_set_homing_current(self, val):
        return self._encode_write_2(0xfa, self.addr, 0x93, val)

    # 设置归零触发参数
    def msg_set_homing_trigger_current(self, sval, eval):
        return self._encode_trigger_data(0xfa, self.addr, 0x51, sval, eval)

    # 设置归零状态
    def msg_set_homing_state(self, status):
        return self._encode_write_1(0xfa, self.addr, 0x55, status)

    # 设置归零模式
    def msg_set_homing_mode(self, mode):
        return self._encode_write_1(0xfa, self.addr, 0x56, mode)

    # 设置脉冲延时
    def msg_set_pulse_delay(self, delay):
        return self._encode_write(0xfa, self.addr, 0x87, delay)

    # 设置堵转超差值
    def msg_set_the_stall_tolerance(self, val):
        return self._encode_write(0xfa, self.addr, 0x89, val)

    # 设置从机地址
    def msg_set_the_slave_address(self, addr):
        return self._encode_write(0xfa, self.addr, 0x8b, addr)

class ClosedLoop:
    def __init__(self, config) -> None:
        pass

# def load_config(config):
#     return ClosedLoopCurrentHelper(config)

def load_config_prefix(config):
    return ClosedLoopCurrentHelper(config)