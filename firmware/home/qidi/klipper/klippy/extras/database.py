import logging
import sqlite3
import os
import json
from contextlib import contextmanager
from typing import Dict, Any

DB_PATH = "/home/qidi/printer_data/database/moonraker-sql.db"

# 使用不可变模板
PRINT_STATE_TEMPLATE = {
    "extruder_target": 0,
    "heater_bed_target": 0,
    "chamber_target": 0,
    "cooling_fan_speed": 0.0,
    "auxiliary_cooling_fan_speed": 0.0,
    "chamber_circulation_fan_speed": 0.0,
    "file_position": 0,
    "plate_index": 1,
    "absolute_coord": 0,
    "absolute_extrude": 0,
    "base_position": [0.0, 0.0, 0.0, 0.0],
    "gcode_position": [0.0, 0.0, 0.0, 0.0],
    "homing_position": [0.0, 0.0, 0.0, 0.0],
    "speed": 0.0,
    "speed_factor": 1.0,
    "extrude_factor": 1.0,
}

class DbReader:
    def __init__(self, config):
        self.printer = config.get_printer()
        self._db_path = DB_PATH
        self._conn = None        
        # 预编译SQL语句
        self._select_stmt = None
        
        # 初始化数据库连接
        self._init_database()
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('READ_PRE_PRINT_STATUS', self.cmd_READ_PRE_PRINT_STATUS)

    def _init_database(self):
        """初始化数据库连接"""
        try:
            db_dir = os.path.dirname(self._db_path)
            if not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
                logging.debug("Created database directory: %s", db_dir)
            
            # 创建连接
            self._conn = sqlite3.connect(
                self._db_path, 
                check_same_thread=False,
                timeout=10.0
            )
            
            self._conn.row_factory = sqlite3.Row
            
            # 预编译查询语句
            self._select_stmt = self._conn.execute(
                "SELECT key, value FROM config WHERE section = 'print_state'"
            )
            
            logging.info("Database reader initialized successfully")
            
        except Exception as e:
            logging.error("Failed to initialize database: %s", e)
            if self._conn:
                try:
                    self._conn.close()
                except:
                    pass
            self._conn = None

    def get_print_state(self) -> Dict[str, Any]:
        """
        获取print_state部分的所有键值对
        使用缓存机制减少数据库查询
        """
        # 从数据库读取
        result = self._read_from_database()
        return result.copy()

    def _read_from_database(self) -> Dict[str, Any]:
        """从数据库读取print_state数据"""
        if self._conn is None:
            logging.warning("Database connection not available, returning template")
            return PRINT_STATE_TEMPLATE.copy()
        
        try:
            # 执行预编译的查询
            cursor = self._conn.execute(
                "SELECT key, value FROM config WHERE section = 'print_state'"
            )
            rows = cursor.fetchall()
            
            # 转换为字典
            raw_result = {}
            for row in rows:
                key, value = row
                raw_result[key] = value
            
            # 规范化结果
            return self._normalize_record(raw_result, PRINT_STATE_TEMPLATE)
            
        except sqlite3.Error as e:
            logging.error("Database read error: %s", e)
            # 尝试重新连接
            try:
                self._conn.close()
            except:
                pass
            self._init_database()
            return PRINT_STATE_TEMPLATE.copy()
        except Exception as e:
            logging.error("Unexpected error reading database: %s", e)
            return PRINT_STATE_TEMPLATE.copy()

    def _normalize_record(self, result: Dict, template: Dict) -> Dict:
        """规范化记录，使用类型安全的转换"""
        normalized = template.copy()  # 创建模板的副本
        
        for key, default_val in template.items():
            if key in result:
                raw_val = result[key]
                
                # 处理None值
                if raw_val is None:
                    continue  # 保持默认值
                    
                # 根据目标类型进行转换
                try:
                    if isinstance(default_val, list):
                        if isinstance(raw_val, list):
                            normalized[key] = raw_val
                        elif isinstance(raw_val, str):
                            try:
                                parsed = json.loads(raw_val)
                                if isinstance(parsed, list):
                                    normalized[key] = parsed
                            except (json.JSONDecodeError, TypeError):
                                pass  # 保持默认值
                    elif isinstance(default_val, (int, float)):
                        # 数值类型转换
                        normalized[key] = type(default_val)(raw_val)
                    elif isinstance(default_val, bool):
                        # 布尔类型转换
                        if isinstance(raw_val, bool):
                            normalized[key] = raw_val
                        elif isinstance(raw_val, (int, str)):
                            normalized[key] = bool(int(raw_val))
                    else:
                        # 其他类型直接赋值
                        normalized[key] = raw_val
                except (ValueError, TypeError) as e:
                    logging.debug("Type conversion failed for key %s: %s", key, e)
                    # 转换失败时保持默认值
                    
        return normalized

    def close(self):
        """关闭数据库连接"""
        if self._conn:
            try:
                self._conn.close()
                logging.debug("Database connection closed")
            except Exception as e:
                logging.error("Error closing database connection: %s", e)
            finally:
                self._conn = None

    def __del__(self):
        """析构函数确保资源清理"""
        self.close()

    def cmd_READ_PRE_PRINT_STATUS(self, gcmd):
        record = self.get_print_state()
        gcmd.respond_raw(f"record:{record}")

def load_config(config):
    return DbReader(config)