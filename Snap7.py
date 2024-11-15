# see also: https://python-snap7.readthedocs.io/en/latest/installation.html
# see also: https://python-snap7.readthedocs.io/en/latest/API/client.html#snap7.client.Client
# see also: https://python-snap7.readthedocs.io/en/latest/API/util.html#snap7.util.set_real
#
# install:
# pip install python-snap7[cli]
# pip install python-snap7
# 
# run server:
# python3 -m snap7.server
# 
# client - read data:
# python3
# import snap7
# client = snap7.client.Client()
# client.connect("127.0.0.1", 0, 0, 1102)
# client.get_connected()
# data = client.db_read(1, 0, 4)
# snap7.util.get_real(data, 0)
#
# client - write data:
# python3
# import snap7
# client = snap7.client.Client()
# client.connect("127.0.0.1", 0, 0, 1102)
# client.get_connected()
# data = bytearray(4)
# snap7.util.set_real(data, 0, 1234.5678)
# client.db_write(1, 0, data)

import time
from tango import AttrQuality, AttrWriteType, DispLevel, DevState, Attr, CmdArgType, UserDefaultAttrProp
from tango.server import Device, attribute, command, DeviceMeta
from tango.server import class_property, device_property
from tango.server import run
import os
import json
from threading import Thread
from threading import Lock
import datetime
import snap7
import re
from json import JSONDecodeError

class Snap7(Device, metaclass=DeviceMeta):
    pass

    host = device_property(dtype=str, default_value="127.0.0.1")
    rack = device_property(dtype=int, default_value=0)
    slot = device_property(dtype=int, default_value=0)
    port = device_property(dtype=int, default_value=102)
    init_dynamic_attributes = device_property(dtype=str, default_value="")
    client = snap7.client.Client()
    dynamicAttributes = {}
    bit_byte_create_lock = Lock()
    bit_byte_locks = {}

    def get_bit_type_lock_for_offset(offset):
        """Get or create a lock for the given offset."""

    @attribute
    def connection_state(self):
        return self.client.get_connected()

    @attribute
    def cpu_state(self):
        return self.client.get_cpu_state()
    
    @attribute
    def time(self):
        return time.time()

    @command(dtype_in=str)
    def add_dynamic_attribute(self, register, topic, 
            variable_type_name="DevString", min_value="", max_value="",
            unit="", write_type_name=""):
        if topic == "": return
        prop = UserDefaultAttrProp()
        variableType = self.stringValueToVarType(variable_type_name)
        writeType = self.stringValueToWriteType(write_type_name)
        if(min_value != "" and min_value != max_value): 
            prop.set_min_value(min_value)
        if(max_value != "" and min_value != max_value): 
            prop.set_max_value(max_value)
        if(unit != ""): 
            prop.set_unit(unit)
        attr = Attr(topic, variableType, writeType)
        attr.set_default_properties(prop)
        register_parts = self.get_register_parts(register)
        self.add_attribute(attr, r_meth=self.read_dynamic_attr, w_meth=self.write_dynamic_attr)
        self.dynamicAttributes[topic] = {"variableType": variableType, "register": register, "register_parts": register_parts, "value": 0 }
        print("added dynamic attribute " + topic)
        print(self.dynamicAttributes[topic])

    def stringValueToVarType(self, variable_type_name) -> CmdArgType:
        if(variable_type_name == "DevBoolean"):
            return CmdArgType.DevBoolean
        if(variable_type_name == "DevLong"):
            return CmdArgType.DevLong
        if(variable_type_name == "DevDouble"):
            return CmdArgType.DevDouble
        if(variable_type_name == "DevFloat"):
            return CmdArgType.DevFloat
        if(variable_type_name == "DevString"):
            return CmdArgType.DevString
        if(variable_type_name == ""):
            return CmdArgType.DevString
        raise Exception("given variable_type '" + variable_type + "' unsupported, supported are: DevBoolean, DevLong, DevDouble, DevFloat, DevString")

    def stringValueToWriteType(self, write_type_name) -> AttrWriteType:
        if(write_type_name == "READ"):
            return AttrWriteType.READ
        if(write_type_name == "WRITE"):
            return AttrWriteType.WRITE
        if(write_type_name == "READ_WRITE"):
            return AttrWriteType.READ_WRITE
        if(write_type_name == "READ_WITH_WRITE"):
            return AttrWriteType.READ_WITH_WRITE
        if(write_type_name == ""):
            return AttrWriteType.READ_WRITE
        raise Exception("given write_type '" + write_type_name + "' unsupported, supported are: READ, WRITE, READ_WRITE, READ_WITH_WRITE")

    @command()
    def plc_cold_start(self):
        self.client.plc_cold_start()

    @command()
    def plc_hot_start(self):
        self.client.plc_hot_start()

    @command()
    def plc_stop(self):
        self.client.plc_stop()

    def read_data_from_area_offset_size(self, area, subarea, offset, size):
        if(area == "DB"): # DB memory
            return self.client.db_read(subarea, offset, size)
        elif(area == "E" or area == "I"): # input memory
            return self.client.eb_read(offset, size)
        elif(area == "A" or area == "Q"): # output memory
            return self.client.ab_read(offset, size)
        else:
            raise Exception("unsupported area type " + area)
    
    def write_data_to_area_offset_size(self, area, subarea, offset, data):
        if(area == "DB"): # DB memory
            self.client.db_write(subarea, offset, data)
        elif(area == "E" or area == "I"): # input memory
            self.client.eb_write(offset, data)
        elif(area == "A" or area == "Q"): # output memory
            self.client.ab_write(offset, data)
        else:
            raise Exception("unsupported area type " + area)

    def bytedata_to_variable(self, data, variableType, offset = 0, suboffset = 0):
        if(variableType == CmdArgType.DevFloat):
            return snap7.util.get_real(data, offset)
        elif(variableType == CmdArgType.DevDouble):
            return snap7.util.get_lreal(data, offset)
        elif(variableType == CmdArgType.DevLong):
            return snap7.util.get_dint(data, offset)
        elif(variableType == CmdArgType.DevBoolean):
            return snap7.util.get_bool(data, offset, suboffset)
        elif(variableType == CmdArgType.DevString):
            return snap7.util.get_string(data, offset)
        else:
            raise Exception("unsupported variable type " + variableType)
    
    def bytes_per_variable_type(self, variableType, customLength = 0):
        if(variableType == CmdArgType.DevFloat):
            return 4
        elif(variableType == CmdArgType.DevDouble):
            return 8
            self.client.set_lreal(data, 0, variable)
        elif(variableType == CmdArgType.DevLong): # 32bit int
            return 4
        elif(variableType == CmdArgType.DevBoolean): # attention! overrides full byte
            return 1
        elif(variableType == CmdArgType.DevString):
            return customLength        
    
    def variable_to_bytedata(self, variable, variableType, suboffset):
        customLength = 0
        if(variableType == CmdArgType.DevString):
            customLength = 256 # reserved default byte area size for a string on s7 devices
        data = bytearray(self.bytes_per_variable_type(variableType, customLength))
        if(variableType == CmdArgType.DevFloat):
            snap7.util.set_real(data, 0, variable)
        elif(variableType == CmdArgType.DevDouble):
            snap7.util.set_lreal(data, 0, variable)
        elif(variableType == CmdArgType.DevLong): # 32bit int
            snap7.util.set_dint(data, 0, variable)
        elif(variableType == CmdArgType.DevBoolean):
            snap7.util.set_bool(data, 0, suboffset, variable)
        elif(variableType == CmdArgType.DevString):
            snap7.util.set_string(data, 0, variable)
        else:
            raise Exception("unsupported variable type " + variableType)
        return data
        
    def get_register_parts(self, register):    
        area = "DB"
        subarea = 0
        offset = 0
        suboffset = 0
        match = re.match(r"^([A-Za-z]+)(\d*)\.(\d+)(?:\.(\d+))?$", register)
        if (not match):
            raise Exception("given register not supported " + register)

        area = match.group(1)
        if(match.group(2) != ""):
            subarea = int(match.group(2))
        offset = int(match.group(3))
        if(not match.group(4) is None and match.group(4) != ""):
            suboffset = int(match.group(4))
        return {"area": area, "subarea": subarea, "offset": offset, "suboffset": suboffset}
    
    def read_dynamic_attr(self, attr):
        name = attr.get_name()
        register_parts = self.dynamicAttributes[name]["register_parts"]
        variableType = self.dynamicAttributes[name]["variableType"]
        customLength = 0
        if(variableType == CmdArgType.DevString):
            customLength = 256 # reserved default byte area size for a string on s7 devices
        size = self.bytes_per_variable_type(variableType, customLength)
        data = self.read_data_from_area_offset_size(register_parts["area"], register_parts["subarea"], register_parts["offset"], size)
        value = self.bytedata_to_variable(data, variableType, 0, register_parts["suboffset"])
        self.debug_stream("read value " + str(name) + ": " + str(value))
        attr.set_value(value)
        self.push_change_event(name, value)

    def write_dynamic_attr(self, attr):
        value = str(attr.get_write_value())
        name = attr.get_name()
        self.dynamicAttributes[name]["value"] = value
        self.publish(name)

    @command(dtype_in=[str])
    def publish(self, name):
        value = self.dynamicAttributes[name]["value"]
        register_parts = self.dynamicAttributes[name]["register_parts"]
        variableType = self.dynamicAttributes[name]["variableType"]
        self.info_stream("Publish variable " + str(name) + ": " + str(value))
        if(variableType == CmdArgType.DevBoolean):
            self.write_boolean_bit(register_parts, value)
        else:
            data = self.variable_to_bytedata(value, variableType, register_parts["suboffset"])
            self.write_data_to_area_offset_size(register_parts["area"], register_parts["subarea"], register_parts["offset"], data)

    def write_boolean_bit(self, register_parts, value):
        offset = register_parts["offset"]
        area = register_parts["area"]
        subarea = register_parts["subarea"]
        bit_index = register_parts["suboffset"]
        if offset not in self.bit_byte_locks:
            with self.bit_byte_create_lock:
                self.bit_byte_locks[offset] = Lock()
        lock = self.bit_byte_locks[offset]
        with lock: # acquire
            data = self.read_data_from_area_offset_size(area, subarea, offset, 1)
            byte = data[0]
            if value:
                # Set the bit
                byte = byte | (1 << bit_index)
            else:
                # Clear the bit
                byte = byte & ~(1 << bit_index)
            data = bytearray([byte])
            self.write_data_to_area_offset_size(area, subarea, offset, data)

    def connect(self):
        self.client.connect(self.host, self.rack, self.slot, self.port)
        if(self.client.get_connected()):
            self.info_stream("Connection established")
        else:
            self.info_stream("Not connected")
        try:
            cpu_info = self.client.get_cpu_info()
            print(cpu_info)
        except Exception as e:
            print("cpu cmd not supported: " + str(e))
        
    def init_device(self):
        self.set_state(DevState.INIT)
        self.get_device_properties(self.get_device_class())
        self.info_stream("Connecting to " + str(self.host) + ":" + str(self.port))
        if self.init_dynamic_attributes != "":
            try:
                attributes = json.loads(self.init_dynamic_attributes)
                for attributeData in attributes:
                    self.add_dynamic_attribute(attributeData["register"], attributeData["name"], 
                        attributeData.get("data_type", ""), attributeData.get("min_value", ""), attributeData.get("max_value", ""),
                        attributeData.get("unit", ""), attributeData.get("write_type", ""))
            except JSONDecodeError as e:
                raise e
        self.connect()
        self.set_state(DevState.ON)

if __name__ == "__main__":
    deviceServerName = os.getenv("DEVICE_SERVER_NAME")
    run({deviceServerName: Snap7})
