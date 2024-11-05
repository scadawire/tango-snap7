# see also: https://python-snap7.readthedocs.io/en/latest/installation.html
# see also: https://python-snap7.readthedocs.io/en/latest/API/client.html#snap7.client.Client

# pip install python-snap7[cli]
# pip install python-snap7

# python3 -m snap7.server

# python3
# import snap7
# client = snap7.client.Client()
# client.connect("127.0.0.1", 0, 0, 1102)
# client.get_connected()
# data = client.db_read(1, 0, 4)
# data[3] = 0b00000001
# client.db_write(1, 0, data)

import time
from tango import AttrQuality, AttrWriteType, DispLevel, DevState, Attr, CmdArgType, UserDefaultAttrProp
from tango.server import Device, attribute, command, DeviceMeta
from tango.server import class_property, device_property
from tango.server import run
import os
import json
from threading import Thread
import datetime
import snap7

class Snap7(Device, metaclass=DeviceMeta):
    pass

    host = device_property(dtype=str, default_value="127.0.0.1")
    rack = device_property(dtype=int, default_value=0)
    slot = device_property(dtype=int, default_value=0)
    port = device_property(dtype=int, default_value=102)
    init_dynamic_attributes = device_property(dtype=str, default_value="")
    client = snap7.client.Client()
    dynamicAttributes = {}
    
    @attribute
    def time(self):
        return time.time()

    @command(dtype_in=str)
    def add_dynamic_attribute(self, topic, 
            variable_type_name="DevString", min_value="", max_value="",
            unit="", write_type_name="", label=""):
        if topic == "": return
        prop = UserDefaultAttrProp()
        variableType = self.stringValueToVarType(variable_type_name)
        writeType = self.stringValueToWriteType(write_type_name)
        self.dynamicAttributeValueTypes[topic] = variableType
        if(min_value != "" and min_value != max_value): 
            prop.set_min_value(min_value)
        if(max_value != "" and min_value != max_value): 
            prop.set_max_value(max_value)
        if(unit != ""): 
            prop.set_unit(unit)
        if(label != ""):
            prop.set_label(label)
        attr = Attr(topic, variableType, writeType)
        attr.set_default_properties(prop)
        self.add_attribute(attr, r_meth=self.read_dynamic_attr, w_meth=self.write_dynamic_attr)
        self.dynamicAttributes[topic] = {variableType: variableType}

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

    def stringValueToTypeValue(self, name, val):
        if(self.dynamicAttributeValueTypes[name] == CmdArgType.DevBoolean):
            if(str(val).lower() == "false"):
                return False
            if(str(val).lower() == "true"):
                return True
            return bool(int(float(val)))
        if(self.dynamicAttributeValueTypes[name] == CmdArgType.DevLong):
            return int(float(val))
        if(self.dynamicAttributeValueTypes[name] == CmdArgType.DevDouble):
            return float(val)
        if(self.dynamicAttributeValueTypes[name] == CmdArgType.DevFloat):
            return float(val)
        return val

    # TODO sync all memory in one go on fixed cron

    def read_dynamic_attr(self, attr):
        name = attr.get_name()
        value = self.dynamicAttributes[name].value
        self.debug_stream("read value " + str(name) + ": " + str(value))
        attr.set_value(value)

    def write_dynamic_attr(self, attr):
        value = str(attr.get_write_value())
        name = attr.get_name()
        self.dynamicAttributes[name].value = value
        self.publish(name)

    @command(dtype_in=[str])
    def publish(self, name):
        value = self.dynamicAttributes[name].value
        register = self.dynamicAttributes[name].register
        variableType = self.dynamicAttributes[name].variableType
        self.info_stream("Publish topic " + str(name) + ": " + str(value))
        # TODO: send to device

    def reconnect(self):
        self.client.connect(self.host, self.rack, self.slot, self.port)
        if(self.client.get_connected()):
            self.info_stream("Connection attempted, waiting for connection result")
        else:
            self.info_stream("Not connected")
        cpu_info = self.client.get_cpu_info()
        print(cpu_info)
        
    def init_device(self):
        self.set_state(DevState.INIT)
        self.get_device_properties(self.get_device_class())
        self.info_stream("Connecting to " + str(self.host) + ":" + str(self.port))
        if self.init_dynamic_attributes != "":
            try:
                attributes = json.loads(self.init_dynamic_attributes)
                for attributeData in attributes:
                    self.add_dynamic_attribute(attributeData["name"], 
                        attributeData.get("data_type", ""), attributeData.get("min_value", ""), attributeData.get("max_value", ""),
                        attributeData.get("unit", ""), attributeData.get("write_type", ""), attributeData.get("label", ""))
            except JSONDecodeError as e:
                raise e
        self.reconnect()

if __name__ == "__main__":
    deviceServerName = os.getenv("DEVICE_SERVER_NAME")
    run({deviceServerName: Snap7})
