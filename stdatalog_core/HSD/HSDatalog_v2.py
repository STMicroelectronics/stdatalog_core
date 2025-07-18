# *****************************************************************************
#  * @file    HSDatalog_v2.py
#  * @author  SRA
# ******************************************************************************
# * @attention
# *
# * Copyright (c) 2022 STMicroelectronics.
# * All rights reserved.
# *
# * This software is licensed under terms that can be found in the LICENSE file
# * in the root directory of this software component.
# * If no LICENSE file comes with this software, it is provided AS-IS.
# *
# *
# ******************************************************************************
#

import math

from datetime import datetime
from dateutil import parser
import json
import os
import dask.dataframe as dd
import struct
import numpy as np
import pandas as pd
from threading import Thread
from werkzeug.serving import make_server

from stdatalog_core.HSD.utils.plot_utils import PlotUtils
from stdatalog_core.HSD_utils.exceptions import *
import stdatalog_core.HSD_utils.logger as logger
from stdatalog_core.HSD.utils.cli_interaction import CLIInteraction as CLI
from stdatalog_core.HSD.utils.file_manager import FileManager
from stdatalog_core.HSD.utils.type_conversion import TypeConversion
from stdatalog_pnpl.DTDL.dtdl_utils import MC_FAST_TELEMETRY_SENSITIVITY, UnitMap
from stdatalog_pnpl.DTDL.device_template_manager import DeviceCatalogManager, DeviceTemplateManager
from stdatalog_pnpl.DTDL.device_template_model import ContentSchema, SchemaType
from stdatalog_pnpl.DTDL.dtdl_utils import DTDL_SENSORS_ID_COMP_KEY, MC_FAST_TELEMETRY_COMP_NAME, MC_SLOW_TELEMETRY_COMP_NAME, AlgorithmTypeEnum, ComponentTypeEnum, SensorCategoryEnum
from plotly.subplots import make_subplots
import plotly.graph_objects as go

log = logger.get_logger(__name__)

class ServerThread(Thread):
    def __init__(self, app, port=8050):
        Thread.__init__(self)
        self.server = make_server('127.0.0.1', port, app.server)
        self.ctx = app.server.app_context()
        self.ctx.push()
        self.running = True

    def run(self):        
        log.debug("Starting Dash server for plotting on port {}...".format(self.server.port))
        self.server.serve_forever()
        log.debug("Dash server has started successfully.")

    def shutdown(self):
        log.debug("Shutting down Dash server for plotting on port {}...".format(self.server.port))
        self.server.shutdown()
        self.running = False
        log.debug("Dash server has been shut down successfully.")

class HSDatalog_v2:
    # Class attributes that will be used to store device model, acquisition info model,
    # and ISPU output format (if any).
    device_model = None
    acq_info_model = None
    ispu_output_format = None
    # Private attribute to store the path to the acquisition folder.
    __acq_folder_path = None
    __checkTimestamps = False
    
    def __init__(self, acquisition_folder = None, update_catalog = True):
        """
        Constructor method for initializing an instance of HSDatalog_v2.

        :param acquisition_folder: [Optional] The path to the folder where acquisition data is stored.
        """        
        # Update the device catalog if the update_catalog flag is set to True
        if update_catalog:
            DeviceCatalogManager.update_catalog()
        # If an acquisition folder is provided, proceed with initialization.
        if acquisition_folder is not None:
            # Attempt to find and load the device configuration from the acquisition folder.
            device_json_file_path = FileManager.find_file("device_config.json", acquisition_folder)
            if device_json_file_path is None:
                # If the device configuration file is missing, raise an error.
                raise MissingSensorModelError
            self.__load_device_from_file(device_json_file_path)
            # Attempt to find and load the acquisition information from the acquisition folder.
            try:
                acquisition_json_file_path = FileManager.find_file("acquisition_info.json", acquisition_folder)
                if acquisition_json_file_path is None:
                    # If the acquisition information file is missing, raise an error.
                    raise MissingAcquisitionInfoError
                self.__load_acquisition_info_from_file(acquisition_json_file_path)
                
                # Attempt to find and load the ISPU output format from the acquisition folder.
                ispu_output_json_file_path = FileManager.find_file("ispu_output_format.json", acquisition_folder)
                if ispu_output_json_file_path is not None:
                    self.__load_ispu_output_format(ispu_output_json_file_path)
                    
            except MissingAcquisitionInfoError:
                # Log an error and raise it if the acquisition information file is missing.
                log.error("No acquisition_info.json file in your Acquisition folder")
                raise
            except MissingDeviceModelError:
                # Raise an error if the device model file is missing.
                raise
        else:
            # If no acquisition folder is provided, log a warning and set the device model and acquisition info to None.
            log.warning("Acquisition folder not provided.")
            self.device_model = None
            self.acq_info_model = None

        # Store the acquisition folder path in a private attribute.
        self.__acq_folder_path = acquisition_folder
        # Data integrity ptocol counter byte size
        self.data_protocol_size = 4
        # A list of colors to be used for line plotting, for example in a graph.
        # self.lines_colors = ['#e6007e', '#a4c238', '#3cb4e6', '#ef4f4f', '#46b28e', '#e8ce0e', '#60b562', '#f99e20', '#41b3ba']
        self.plot_threads: list[ServerThread] = []
    
    #========================================================================================#
    ### Data Analisys ########################################################################
    #========================================================================================#

    ### ==> Debug ###
    def enable_timestamp_recovery(self, status):
        """Enable timestamp recovery algorithm.

        Args:
            status (bool): True to enable, False elsewhere
        """
        self.__checkTimestamps = status
    ### Debug <== ###
    
    def __load_device_from_file(self, device_json_file_path, device_id = 0):
        """Function to load a device_config.json file (Device Current Status)

        Args:
            device_json_file_path (str): device_config.json path

        Raises:
            MissingDeviceModelError: Exception returned if an error occour in device_config.json loading
        """        
        try:
            with open(device_json_file_path, encoding="UTF-8") as f:
                file_content = f.read()
                if file_content[-1] == '\x00':
                    device_json_dict = json.loads(file_content[:-1])
                else:
                    device_json_dict = json.loads(file_content)
            # device_json_str = json.dumps(device_json_dict)
            f.close()
            self.__load_device(device_json_dict, device_id, True)
        except MissingDeviceModelError as e:
            raise e

    def __load_acquisition_info_from_file(self, acq_info_json_file_path):
        """Function to load a acquisition_info.json file (Acquisition_Info Component Current Status)

        Args:
            acq_info_json_file_path ([str]): acquisition_info.json path

        Raises:
            MissingAcquisitionInfoError: Exception returned if an error occour in acqusition_info.json loading
        """
        try:
            with open(acq_info_json_file_path) as f:
                file_content = f.read()
                if file_content[-1] == '\x00':
                    acq_info_json_dict = json.loads(file_content[:-1])
                else:
                    acq_info_json_dict = json.loads(file_content)
            acq_info_json_str = json.dumps(acq_info_json_dict)
            f.close()
            self.acq_info_model = json.loads(acq_info_json_str)
        except:
            raise MissingAcquisitionInfoError

    def __get_sensor_unit_from_dtdl(self, prop_w_unit_name, comp_dtdl_contents):
        property = [c for c in comp_dtdl_contents if c.name == prop_w_unit_name]
        if property is not None and len(property) > 0:
            unit = property[0].unit
            if unit is not None:
                return unit
            display_unit = property[0].display_unit
            if display_unit is not None:
                return display_unit if isinstance(display_unit, str) else display_unit.en
        return None
    
    def __load_ispu_output_format(self, ispu_output_format_file_path):
        """Function to load a ispu_output_format.json file

        Args:
            ispu_output_format_file_path ([str]): ispu_output_format.json path
        """
        with open(ispu_output_format_file_path) as f:
            file_content = f.read()
            if file_content[-1] == '\x00':
                ispu_out_json_dict = json.loads(file_content[:-1])
            else:
                ispu_out_json_dict = json.loads(file_content)
        ispu_out_json_str = json.dumps(ispu_out_json_dict)
        f.close()
        self.ispu_output_format = json.loads(ispu_out_json_str)

    @staticmethod
    def __convert_prop_enum_in_value(prop_enum_index, prop_content):
        enum_dname = prop_content.schema.enum_values[prop_enum_index].display_name
        value = enum_dname if isinstance(enum_dname,str) else enum_dname.en
        if prop_content.schema.value_schema.value == "integer":
            num_value = value.replace(',','').replace('.','')
            if num_value.isnumeric():
                return float(value.replace(',','.'))
        return value

    def __convert_enums_in_values(self, comp_dtdl_contents, comp_status):
        for property in comp_dtdl_contents:
            if property.schema is not None and isinstance(property.schema, ContentSchema) and property.schema.type == SchemaType.ENUM :
                enum_index = comp_status[property.name]
                prop_value = self.__convert_prop_enum_in_value(enum_index, property)
                comp_status[property.name] = prop_value
    
    def __load_device(self, device_dict, device_id = 0, from_file = True):
        
        if from_file:
            self.device_model = device_dict['devices'][device_id]
        else:
            self.device_model = device_dict

        board_id = hex(self.device_model["board_id"])
        fw_id = hex(self.device_model["fw_id"])
        
        dev_template_json = DeviceCatalogManager.query_dtdl_model(board_id, fw_id)
        if isinstance(dev_template_json,dict):
            if dev_template_json == {}:
                raise MissingDeviceModelError(board_id,fw_id)
            components = self.device_model.get("components")
            fw_name = None
            for c in components:
                if c.get("firmware_info") is not None:
                    fir_info = c.get("firmware_info")
                    if fir_info.get("fw_name") is not None:
                        fw_name = fir_info.get("fw_name")
            # fw_name = self.device_model.get("components").get("firmware_info").get("fw_name")
            if fw_name is not None:
                splitted_fw_name = fw_name.lower().split("-")
                reformatted_fw_name = "".join([splitted_fw_name[0]] + [f.capitalize() for f in splitted_fw_name[1:]])
                for dt in dev_template_json:
                    if reformatted_fw_name.lower() in  dev_template_json[dt][0].get("@id").lower():
                        dev_template_json = dev_template_json[dt]
                        break
        dt_manager = DeviceTemplateManager(dev_template_json)
        self.components_dtdl = dt_manager.get_components()
        for comp_name in self.components_dtdl.keys():
            comp_status = [c for c in self.device_model.get("components") if list(c.keys())[0] == comp_name]
            if len(comp_status)>0:
                comp_status = comp_status[0].get(comp_name)
                comp_dtdl_contents = [c for c in self.components_dtdl[comp_name].contents]
                c_type = comp_status.get("c_type")
                if c_type == ComponentTypeEnum.SENSOR.value:
                    s_category = comp_status.get("sensor_category")
                    if s_category == SensorCategoryEnum.ISENSOR_CLASS_MEMS.value:
                        comp_status["unit"] = self.__get_sensor_unit_from_dtdl("fs", comp_dtdl_contents)
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_AUDIO.value:
                        comp_status["unit"] = self.__get_sensor_unit_from_dtdl("aop", comp_dtdl_contents)
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_RANGING.value:
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_CAMERA.value:
                        pass
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_PRESENCE.value:
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    elif s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value:
                        if from_file:
                            self.__convert_enums_in_values(comp_dtdl_contents, comp_status)
                    else: #Retrocompatibility
                        if ":"+DTDL_SENSORS_ID_COMP_KEY+":" in self.components_dtdl[comp_name].id: #"sensors":
                            s_info_contents = [c for c in self.components_dtdl[comp_name].contents if c.name == "odr" or c.name == "fs" or c.name == "aop"]
                            comp_status = [x for x in self.device_model["components"] if list(x.keys())[0] == comp_name]
                            if len(comp_status) > 0:
                                if len(s_info_contents) > 0:
                                    for sc in s_info_contents:
                                        if (sc.name == "fs" or sc.name == "aop") and sc.unit is not None:
                                            comp_status[0][comp_name]["unit"] = sc.unit
                                        elif (sc.name == "fs" or sc.name == "aop") and sc.display_unit is not None:
                                            display_unit = sc.display_unit if isinstance(sc.display_unit, str) else sc.display_unit.en
                                            comp_status[0][comp_name]["unit"] = display_unit
                                        if from_file and sc.schema is not None and isinstance(sc.schema, ContentSchema) and sc.schema.type == SchemaType.ENUM :
                                            enum_index = comp_status[0][comp_name][sc.name]
                                            prop_value = float(self.__convert_prop_enum_in_value(enum_index, sc))
                                            comp_status[0][comp_name][sc.name] = prop_value
                                else:
                                    if "unit" not in comp_status[0][comp_name]:
                                        comp_status[0][comp_name]["unit"] = ""
        log.debug(f"Device Model: {self.device_model}")

    def get_file_dimension(self, component_name):
        filepath = os.path.join(self.__acq_folder_path, f"{component_name}.dat")
        if os.path.isfile(filepath):
            return os.path.getsize(filepath)
        else:
            return None

    def get_data_protocol_size(self):
        return self.data_protocol_size

    def get_acquisition_path(self):
        return self.__acq_folder_path

    def get_device(self):
        """
        Retrieves the current device model from the HSDatalog instance.
        This method returns the device model that has been set for the instance, which contains information about the device.

        :return: The device model object or dictionary that holds the device's information.
        """
        # Return the device_model attribute of the HSDatalog instance.
        # This attribute should hold the current device's information.
        return self.device_model

    def set_device(self, new_device, device_id = 0, from_file = True):
        """
        Sets the device model for the HSDatalog instance and loads the device configuration.

        This method updates the device model with the new device information provided and calls an internal method to load the device configuration.
        It also logs information about the firmware, including the alias, firmware name, version, and serial or part number if available.
        This function allows to change the current HSD Device Status
            e.g.: if using hsd_link you obtain the current device template directly from the board, you can set it in your HSDatalog instance.

        :param new_device: A dictionary containing the new device information, typically obtained from a device template or directly from the board.
        """
        
        if from_file:
            # Update the device_model attribute with the first device from the new_device dictionary
            self.device_model = new_device['devices'][device_id]
        else:
            self.device_model = new_device
         # Call the private method '__load_device' to load the device configuration
        self.__load_device(new_device, device_id, from_file)
        # Retrieve firmware information from the device
        fw_info = self.get_firmware_info()["firmware_info"]
        # Log the device information, including alias, firmware name, and version
        # Check if part number is available and include it in the log message
        if "part_number" in fw_info:
            log.info("Device [{}] - {} v{} sn:{} loaded correctly!".format(fw_info['alias'], fw_info['fw_name'], fw_info['fw_version'], fw_info['part_number']))
        # If part number is not available, check for serial number and include it in the log message
        elif "serial_number" in fw_info:
            log.info("Device [{}] - {} v{} sn:{} loaded correctly!".format(fw_info['alias'], fw_info['fw_name'], fw_info['fw_version'], fw_info['serial_number']))
         # If neither part number nor serial number is available, log the information without them
        else:
            log.info("Device [{}] - {} v{} loaded correctly!".format(fw_info['alias'], fw_info['fw_name'], fw_info['fw_version']))

    def get_device_info(self):
        """
        Retrieves the device information component from the HSDatalog instance.
        This method calls another method, `get_component`, with the argument "DeviceInformation" to obtain detailed information about the device.
        The "DeviceInformation" component typically includes metadata such as the device's name, type, serial number, firmware version, and other relevant details.

        :return: The "DeviceInformation" component of the device, which is a dictionary or object containing detailed device information.
        """
        # Call the 'get_component' method with the argument "DeviceInformation" to retrieve the device information.
        # The 'get_component' method is expected to be implemented elsewhere in the HSDatalog class and should return the requested component.
        return self.get_component("DeviceInformation")
    
    #HSD2 only
    def get_firmware_info(self):
        """This fuction returns the current Status of the firmware_info Component
           DTDL Component name: firmware_info
        Returns:
            dict: firmware_info Component current Status if it exists, None elsewhere
        """      
        return self.get_component("firmware_info")

    #HSD2 here new_device_info is a json(dict) --> put new_device:DeviceInfo only in HSDv1 function definition
    def set_device_info(self, new_device_info):
        """AI is creating summary for set_device_info

        Args:
            new_device_info ([type]): [description]
        """        
        self.device_info = new_device_info

    def get_component(self, component_name):
        """This fuction returns the current Status of the {comp_name} Component

        Args:
            component_name (str): DTDL Component name

        Raises:
            MissingComponentModelError: Exception raised if the {comp_name} Component does not exist
            MissingDeviceModelError: Exception raised if the current device status does not exist

        Returns:
            dict: {comp_name} Component current Status if it exists, None elsewhere
        """        
        if self.device_model is not None:
            components = self.device_model['components']
            for c in components:
                if list(c)[0] == component_name:
                    return c
            log.error("No Model loaded for {} Component".format(component_name))
            raise MissingComponentModelError
        else:
            log.error("No Device Model loaded!")
            raise MissingDeviceModelError
    
    #missing
    #def get_sub_sensor(self, sensor_name, ss_id = None, ss_type = None):
    
    #missing
    #def get_sub_sensors(self, sensor_name, only_active = False):
    
    def get_sensor_list(self, type_filter = "", only_active = False):
        active_sensors = []
        sensor_list = self.device_model['components']
        for s in sensor_list:
            for element in s[list(s)[0]]:
                if element == 'c_type' and s[list(s)[0]]['c_type'] == ComponentTypeEnum.SENSOR.value:
                    if type_filter == "":
                        if only_active:
                            if "enable" in s[list(s)[0]] and s[list(s)[0]]['enable'] == True:
                                active_sensors.append(s)
                        else:
                            active_sensors.append(s)
                    else:
                        sensor_type = str(list(s)[0]).lower().split("_")[-1]
                        if only_active:
                            if "enable" in s[list(s)[0]] and s[list(s)[0]]['enable'] == True and sensor_type == type_filter.lower():
                                active_sensors.append(s)
                        else:
                            if sensor_type == type_filter.lower():
                                active_sensors.append(s)
        return active_sensors

    def get_algorithm_list(self, type_filter = "", only_active = False):
        active_algos = []
        algo_list = self.device_model['components']
        for s in algo_list:
            for element in s[list(s)[0]]:
                if element == 'c_type' and s[list(s)[0]]['c_type'] == ComponentTypeEnum.ALGORITHM.value:
                    if type_filter == "":
                        if only_active:
                            if "enable" in s[list(s)[0]] and s[list(s)[0]]['enable'] == True:
                                active_algos.append(s)
                        else:
                            active_algos.append(s)
                    else:
                        sensor_type = str(list(s)[0]).lower().split("_")[-1]
                        if only_active:
                            if "enable" in s[list(s)[0]] and s[list(s)[0]]['enable'] == True and sensor_type == type_filter.lower():
                                active_algos.append(s)
                        else:
                            if sensor_type == type_filter.lower():
                                active_algos.append(s)
        return active_algos
    
    def get_actuator_list(self, only_active = False):
        active_actuators = []
        actuator_list = self.device_model['components']
        for ac in actuator_list:
            for element in ac[list(ac)[0]]:
                if element == 'c_type' and ac[list(ac)[0]]['c_type'] == ComponentTypeEnum.ACTUATOR.value:
                    if only_active:
                        if "enable" in ac[list(ac)[0]] and ac[list(ac)[0]]['enable'] == True:
                            active_actuators.append(ac)
                    else:
                        active_actuators.append(ac)
        return active_actuators

    def get_sw_tag_classes(self):
        if self.device_model is not None:
            tags_info_dict = self.get_component("tags_info")
            if tags_info_dict is not None:
                return {key: value for key, value in tags_info_dict['tags_info'].items() if "sw_tag" in key}
            else:
                return {}

    def get_hw_tag_classes(self):
        if self.device_model is not None:
            tags_info_dict = self.get_component("tags_info")
            if tags_info_dict is not None:
                return {key: value for key, value in tags_info_dict['tags_info'].items() if "hw_tag" in key}
            else:
                return None

    def get_acquisition_info(self):
        return self.acq_info_model

    #HSD2 here new_device_info is a json(dict) --> put new_device:DeviceInfo only in HSDv1 function definition
    def set_acquisition_info(self, new_acquisition_info):
        self.acq_info_model = new_acquisition_info

    def get_acquisition_interface(self):
        return self.acq_info_model['interface']
    
    def get_acquisition_label_classes(self):
        if self.acq_info_model is not None:
            if "tags" in self.acq_info_model:
                return sorted(set(dic['l'] for dic in self.acq_info_model['tags']))
            else:
                log.warning("No defined tag classes in Acquisition Information Component.")
                return []
        log.warning("Empty Acquisition Info model.")
        # raise MissingAcquisitionInfoError
        return None

    def get_time_tags(self, which_tags = None):
        time_labels = []
        if self.acq_info_model is not None:
            acq_start_time = self.acq_info_model['start_time']
            acq_end_time = self.acq_info_model['end_time']
            self.s_t = datetime.strptime(acq_start_time, '%Y-%m-%dT%H:%M:%S.%fZ')
            tags = self.acq_info_model['tags']

            if which_tags is not None:
                tags = [tag for tag in tags if tag['l'] in which_tags]

            for lbl in self.get_acquisition_label_classes():
                # start_time, end_time are vectors with the corresponding 't' entries in DataTag-json
                start_time = np.array([t['ta'] for t in tags if t['l'] == lbl and t['e']])
                end_time = np.array([t['ta'] for t in tags if t['l'] == lbl and not t['e']])
                # now must associate at each start tag the appropriate end tag
                # (some may be missing because of errors in the tagging process)
                for tstart in start_time:
                    tag = {}
                    jj = [i for (i, n) in enumerate(end_time) if n >= tstart]
                    if jj:
                        tend = end_time[min(jj)]
                    else:
                        tend = acq_end_time  # if no 'end tag' found the end is eof
                    tag['label'] = lbl
                    tag['time_start'] = (datetime.strptime(tstart, '%Y-%m-%dT%H:%M:%S.%fZ') - self.s_t).total_seconds()
                    tag['time_end'] = (datetime.strptime(tend, '%Y-%m-%dT%H:%M:%S.%fZ') - self.s_t).total_seconds()
                    time_labels.append(tag)
            return time_labels
        else:
            log.error("Empty Acquisition Info model.")
            raise MissingAcquisitionInfoError

    # Helper function to convert ISO8601 time strings to seconds
    def get_seconds_from_ISO8601(self, start_time_str, end_time_str):
        start_time = parser.isoparse(start_time_str)
        end_time = parser.isoparse(end_time_str)
        duration = (end_time - start_time).total_seconds()
        return duration

    # Get tags dictionary list from acquisition_info.json file
    def get_tags(self):
        tags = []
        acq_start_time = self.acq_info_model["start_time"]
        acq_end_time = self.acq_info_model["end_time"]
        tags_array = self.acq_info_model["tags"]
        acq_duration = self.get_seconds_from_ISO8601(acq_start_time, acq_end_time)

        tag_labels = []
        for i in range(len(tags_array)):
            start_tag = tags_array[i]
            if start_tag["e"]:
                tag_label = start_tag["l"]
                tag_start_time = start_tag["ta"]
                tag_start_seconds = self.get_seconds_from_ISO8601(acq_start_time, tag_start_time)
                tag_end_seconds = acq_duration
                for j in range(i, len(tags_array)):
                    end_tag = tags_array[j]
                    if end_tag["l"] == tag_label and not end_tag["e"]:
                        tag_end_time = end_tag["ta"]
                        tag_end_seconds = self.get_seconds_from_ISO8601(acq_start_time, tag_end_time)
                        break
                if tag_label in tag_labels:
                    # Already present in tags list --> ADD tag_times!
                    for tag in tags:
                        if tag["label"] == tag_label:
                            tag["times"].append((tag_start_seconds, tag_end_seconds))
                else:
                    tag_labels.append(tag_label)
                    tags.append({"label": tag_label, "times": [(tag_start_seconds, tag_end_seconds)]})

        return tags
    
    def __get_active_mc_telemetries_names(self, ss_stat, comp_name):
        if comp_name == "slow_mc_telemetries":
            desc_telemetry = ss_stat.get("st_ble_stream")
            return [st for st in desc_telemetry if isinstance(desc_telemetry[st],dict) and desc_telemetry[st].get("enable")==True]
        elif comp_name == "fast_mc_telemetries":
            return [k for k in ss_stat.keys() if isinstance(ss_stat[k],dict) and k != "sensitivity" and ss_stat[k]["enabled"] == True]
    
    def __process_datalog(self, sensor_name, ss_stat, raw_data, dataframe_size, timestamp_size, raw_flag = False, start_time = None, prev_timestamp = None):

        #####################################################################
        def extract_data_and_timestamps(start_time):
        
            """ gets data from a file .dat
                np array with one column for each axis of each active subSensor
                np array with sample times
            """
            sensor_name_contains_mlc_ispu = "_mlc" in sensor_name or "_ispu" in sensor_name
            
            if c_type == ComponentTypeEnum.SENSOR.value:
                s_category = ss_stat.get("sensor_category")
                if self.__checkTimestamps == True:
                    check_timestamps = not sensor_name_contains_mlc_ispu
                else:
                    check_timestamps = False
                if s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                    odr = 1/(ss_stat.get("intermeasurement_time")/1000) if ss_stat.get("intermeasurement_time") > ss_stat.get("exposure_time")/1000 + 6  else (1/((ss_stat.get("exposure_time")/1000 + 6)/1000))
                    frame_period = 0 if sensor_name_contains_mlc_ispu else samples_per_ts / odr
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value:
                    frame_period = 0 if sensor_name_contains_mlc_ispu else samples_per_ts / (1/(ss_stat.get("adc_conversion_time")/1000000))
                else:
                    measodr = ss_stat.get("measodr")
                    if measodr is None or measodr == 0:
                        measodr = ss_stat.get("odr")
                    frame_period = 0 if sensor_name_contains_mlc_ispu else samples_per_ts / measodr
            elif c_type == ComponentTypeEnum.ALGORITHM.value:
                check_timestamps = False
                if ss_stat.get("algorithm_type") == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                    fft_sample_freq = ss_stat.get("fft_sample_freq")
                    frame_period = samples_per_ts / fft_sample_freq
                else:
                    frame_period = 0
                algo_type = ss_stat.get("algorithm_type")
            elif c_type == ComponentTypeEnum.ACTUATOR.value:
                check_timestamps = False
                if "samples_per_ts" not in ss_stat:
                    frame_period = 0
                else:
                    frame_period = samples_per_ts / ss_stat.get("odr")

            # rndDataBuffer = raw_data rounded to an integer # of frames
            rnd_data_buffer = raw_data[:int(frame_size * num_frames)]

            if start_time != 0:
                timestamp_first = start_time #TODO check with spts != 0
            else:
                timestamp_first = ss_stat.get('ioffset', 0)
            timestamps = []
            data_type = TypeConversion.get_np_dtype(data_type_string)
            data = np.zeros((data1D_per_frame * num_frames, 1), dtype=data_type)

            if timestamp_size != 0:
                for ii in range(num_frames):  # For each Frame:
                    start_frame = ii * frame_size
                    # segment_data = data in the current frame
                    segment_data = rnd_data_buffer[start_frame:start_frame + dataframe_size]
                    if data_type_string == "int24" or data_type_string == "int24_t":
                        segment_data = TypeConversion.int24_buffer_to_int32_buffer(segment_data)

                    # segment_tS = ts is at the end of each frame
                    segment_ts = rnd_data_buffer[start_frame + dataframe_size:start_frame + frame_size]

                    # timestamp of current frame
                    timestamps.append(np.frombuffer(segment_ts, dtype='double')[0])

                    # Data of current frame
                    data_range = slice(ii * data1D_per_frame, (ii + 1) * data1D_per_frame)
                    data[data_range, 0] = np.frombuffer(segment_data, dtype=data_type)

                    # Check Timestamp consistency
                    if check_timestamps and ii > 0:
                        delta_ts = abs(timestamps[ii] - timestamps[ii - 1])
                        if delta_ts < 0.1 * frame_period or delta_ts > 10 * frame_period or np.isnan(timestamps[ii]) or np.isnan(timestamps[ii - 1]):
                            data[data_range, 0] = 0
                            timestamps[ii] = timestamps[ii - 1] + frame_period
                            log.warning("Sensor {}: corrupted data at {}".format(sensor_name, "{} sec".format(timestamps[ii])))
            else:                
                if data_type_string == "int24" or data_type_string == "int24_t":
                    rnd_data_buffer = TypeConversion.int24_buffer_to_int32_buffer(rnd_data_buffer)
                data = np.frombuffer(rnd_data_buffer, dtype=data_type)
                is_first_chunk = ss_stat.get("is_first_chunk", False)
                if is_first_chunk:
                    start_time = timestamp_first
                    stop_time = timestamp_first + (num_frames * frame_period)
                else:
                    start_time = timestamp_first + frame_period
                    stop_time = timestamp_first + frame_period + (num_frames * frame_period)
                
                timestamps = np.arange(
                    start=start_time,
                    stop=stop_time,
                    step=frame_period,
                    dtype=np.float64
                )

                timestamps = timestamps[:num_frames]

            if c_type == ComponentTypeEnum.SENSOR.value:
                s_dim = ss_stat.get('dim',1)
                if raw_flag:
                    s_data = np.reshape(data, (-1, 64 if "_ispu" in sensor_name else s_dim))
                else:
                    s_data = np.reshape(data, (-1, 64 if "_ispu" in sensor_name else s_dim)).astype(dtype=np.byte if "_ispu" in sensor_name else np.float32)
                    sensitivity = float(ss_stat.get('sensitivity', 1))
                    np.multiply(s_data, sensitivity, out = s_data, casting='unsafe')
            elif c_type == ComponentTypeEnum.ALGORITHM.value:
                if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                    s_data = np.reshape(data, (-1, ss_stat['fft_length'])).astype(dtype=np.float32)
                if not raw_flag:
                    sensitivity = float(ss_stat.get('sensitivity', 1))
                    np.multiply(s_data, sensitivity, out = s_data, casting='unsafe')
            elif c_type == ComponentTypeEnum.ACTUATOR.value:
                if sensor_name == MC_SLOW_TELEMETRY_COMP_NAME or sensor_name == MC_FAST_TELEMETRY_COMP_NAME:
                    active_fast_telemetries = self.__get_active_mc_telemetries_names(ss_stat, sensor_name)
                    nof_telemetries = len(active_fast_telemetries)
                    s_data = np.reshape(data, (-1, nof_telemetries)).astype(dtype=np.float32)
                if sensor_name == MC_FAST_TELEMETRY_COMP_NAME and not raw_flag: 
                    scaler_current = ss_stat[MC_FAST_TELEMETRY_SENSITIVITY]['current']
                    scaler_voltage = ss_stat[MC_FAST_TELEMETRY_SENSITIVITY]['voltage']
                    for idx, t in enumerate(active_fast_telemetries):
                        if "i" in t:
                            s_data[:,idx] = s_data[:,idx] * scaler_current
                        elif "v" in t:
                            s_data[:,idx] = s_data[:,idx] * scaler_voltage 

            
            if len(data) == 0:
                return [],[]
            
            # if c_type == ComponentTypeEnum.SENSOR.value or c_type == ComponentTypeEnum.ALGORITHM.value:
            # samples_time: numpy array of 1 clock value per each data sample
            if samples_per_ts > 1:
                # initial_offset is relevant
                frames = num_frames
                ioffset = ss_stat.get('ioffset', 0)
                is_first_chunk = ss_stat.get("is_first_chunk", False)

                if start_time != 0 and is_first_chunk:
                    if prev_timestamp is not None:
                        timestamps = np.insert(timestamps, 0, prev_timestamp)
                    else:            
                        timestamps = np.insert(timestamps, 0, ioffset)
                else:
                    timestamps = np.append(ioffset, timestamps)

                ss_stat["ioffset"] = timestamps[-1] #NOTE! Update the ioffset with the last extracted timestamp to allow eventual batch processing (this will be the start timestamp to continue the linear interpolation for the next chunk)
                samples_times = np.zeros((frames * samples_per_ts, 1))
                samples_times.fill(-1)

                # sample times between timestamps are linearly interpolated
                for ii in range(frames): # For each Frame:
                    delta_ts = abs(timestamps[ii+1] - timestamps[ii])
                    if frame_period > 0 and delta_ts > frame_period + frame_period * 0.33:
                        samples_times[ii * samples_per_ts:(ii + 1) * samples_per_ts, 0] = np.linspace(timestamps[ii + 1]-frame_period, timestamps[ii + 1], samples_per_ts, endpoint= False)
                    else:
                        samples_times[ii * samples_per_ts:(ii + 1) * samples_per_ts, 0] = np.linspace(timestamps[ii], timestamps[ii + 1], samples_per_ts, endpoint= False)
            else:
                # if samples_per_ts is 1, the timestamps coincides with the sample timestamp
                # initial offset and interpolation is not relevant anymore
                samples_times = np.array(timestamps).reshape(-1, 1)
                if len(timestamps) > 0:
                    ss_stat["ioffset"] = timestamps[-1] #NOTE! Update the ioffset with the last extracted timestamp to allow eventual batch processing (this will be the start timestamp to continue the linear interpolation for the next chunk)
            
            valid_indices = samples_times != -1
            samples_times = samples_times[valid_indices.flatten()]
            s_data = s_data[valid_indices.flatten()]

            return s_data, samples_times
        #####################################################################
        
        c_type = ss_stat.get("c_type")

        # size of the frame. A frame is data + ts
        frame_size = dataframe_size + timestamp_size

        # number of frames = round down (//) len datalog // frame_size
        num_frames = len(raw_data) // frame_size
        
        # force int8 data_type for ISPU
        data_type_string = "int8" if "_ispu" in sensor_name else ss_stat['data_type']
        data_type_byte_num = TypeConversion.check_type_length(data_type_string)

        # data1D_per_frame = number of data samples in 1 frame
        # must be the same as samplePerTs * number of axes
        data1D_per_frame = int(dataframe_size / data_type_byte_num)

        #samples per timestamp
        spts = ss_stat.get('samples_per_ts', {})
        if isinstance(spts, int):
            samples_per_ts = spts
        else:
            samples_per_ts = spts.get('val', 0)
        
        if c_type == ComponentTypeEnum.SENSOR.value:
            samples_per_ts = samples_per_ts or int(data1D_per_frame / ss_stat.get('dim', 1))
        if c_type == ComponentTypeEnum.ALGORITHM.value:
            algo_type = ss_stat.get("algorithm_type")
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                samples_per_ts = 1#samples_per_ts or int(data1D_per_frame / ss_stat.get('dim', 1))
        elif c_type == ComponentTypeEnum.ACTUATOR.value:
            if sensor_name == "slow_mc_telemetries":
                desc_telemetry = ss_stat.get("st_ble_stream")
                nof_telemetries = len([st for st in desc_telemetry if isinstance(desc_telemetry[st],dict) and desc_telemetry[st].get("enable")==True])
            elif sensor_name == "fast_mc_telemetries":
                nof_telemetries = len([k for k in ss_stat.keys() if isinstance(ss_stat[k],dict) and k != "sensitivity" and ss_stat[k]["enabled"] == True])
            if "samples_per_ts" not in ss_stat:
                samples_per_ts = dataframe_size // data_type_byte_num // nof_telemetries

        return extract_data_and_timestamps(start_time)
    
    def get_sensor(self, sensor_name):
        ss_stat = self.get_component(sensor_name)
        return ss_stat
    
    def __get_sensor_status(self, sensor_name):
        ss_stat = self.get_component(sensor_name)
        return ss_stat[sensor_name]
    
    def __get_sensor_file_path(self, sensor_name):
        file_path = os.path.join(self.__acq_folder_path, FileManager.encode_file_name(sensor_name))
        if not os.path.exists(file_path):
            log.error("No such file or directory: {} found for {} sensor".format(file_path, sensor_name))
            raise MissingFileForSensorError(file_path, sensor_name)
        return file_path
    
    def __get_checked_sensor_file_path(self, sensor_name):
        file_path = os.path.join(self.__acq_folder_path, FileManager.encode_file_name(sensor_name + "_checked"))
        if not os.path.exists(file_path):
            log.error("No such file or directory: {} found for {} sensor".format(file_path, sensor_name + "_checked"))
            raise MissingFileForSensorError(file_path, sensor_name)
        return file_path

    def remove_4bytes_every_n_optimized(self, arr, N):
        # Create a boolean mask for the elements to keep
        mask = np.ones(len(arr), dtype=bool)
        for start in range(0, len(arr), N):
            mask[start:start+self.data_protocol_size] = False

        # Apply the mask to get the new array
        new_arr = arr[mask]
        
        # Concatenate the remaining slices and return the result
        return new_arr

    def get_data_and_timestamps_batch(self, comp_name, comp_status, start_time = 0, end_time = -1, raw_flag = False):
        
        log.debug("Data & Timestamp extraction algorithm STARTED...")

        # get acquisition interface
        interface = self.acq_info_model['interface']
        
        c_type = comp_status.get("c_type")

        data_protocol_size = self.get_data_protocol_size()
        data_packet_size = 0
        # data packet size (0:sd card, 1:usb, 2:ble, 3:serial)
        if interface == 0:
            data_packet_size = comp_status["sd_dps"] - data_protocol_size
        elif interface == 1:
            data_packet_size = comp_status["usb_dps"]
        elif interface == 2:
            data_packet_size = comp_status["ble_dps"]
        elif interface == 3:
            data_packet_size = comp_status["serial_dps"]
        else:
            log.error(f"Unknown interface: {interface}. check your device_config.json file")
            raise
        
        # get dat file path and size (obtained from "sensor_name + sub_sensor_type")
        file_path = self.__get_sensor_file_path(comp_name)
        file_size = os.path.getsize(file_path)

        cmplt_pkt_size = data_packet_size + data_protocol_size
        nof_data_packet = file_size // cmplt_pkt_size # "//" math.floor equivalent #CEIL

        raw_data_array = np.array([], dtype='uint8')
        
        if c_type == ComponentTypeEnum.ALGORITHM.value:
            algo_type = comp_status.get("algorithm_type")
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                # get FFT algo "dimensions" --> FFT Length
                s_dim = comp_status.get("fft_length")
            else:
                s_dim = comp_status.get('dim')
        else:
            # get sensor dimensions
            s_dim = comp_status.get('dim', 1)
        
        # get Data type byte length
        s_data_type_len = TypeConversion.check_type_length(comp_status['data_type'])
        
        # get samples per ts
        spts = comp_status.get('samples_per_ts', {})
        if isinstance(spts, int):
            s_samples_per_ts = spts
        else:
            if c_type == ComponentTypeEnum.ACTUATOR.value:
                s_samples_per_ts = spts.get('val', 1)
            else:
                s_samples_per_ts = spts.get('val', 0)
        
        if c_type == ComponentTypeEnum.SENSOR.value or \
          (c_type == ComponentTypeEnum.ACTUATOR.value and "odr" in comp_status):

            if s_samples_per_ts != 0:
                dataframe_byte_size = s_samples_per_ts * s_dim * s_data_type_len
                timestamp_byte_size = 8
            else:
                dataframe_byte_size = s_dim * s_data_type_len
                timestamp_byte_size = 0

            # 1 sec --> ODR samples --> ODR * dim * data_type Bytes
            odr = comp_status.get("measodr")
            if odr is None or odr == 0:
                odr = comp_status.get("odr", 1)

            tot_counters_bytes = nof_data_packet * data_protocol_size
            tot_file_data_and_times_bytes = file_size - tot_counters_bytes
            tot_timestamps_bytes = (tot_file_data_and_times_bytes // (dataframe_byte_size+timestamp_byte_size))*timestamp_byte_size
            tot_data_bytes = tot_file_data_and_times_bytes - tot_timestamps_bytes
            tot_data_samples = int(tot_data_bytes/(s_data_type_len*s_dim))

            
            start_sample_idx = math.floor(odr*start_time)
            start_data_bytes_idx = start_sample_idx * s_data_type_len * s_dim
            if s_samples_per_ts != 0:                
                nof_timestamps_in_start = math.floor(start_sample_idx/s_samples_per_ts)
            else:
                nof_timestamps_in_start = 0
                sample_end = int(odr * end_time) if end_time != -1 else -1
                if sample_end > tot_data_samples or sample_end == -1:
                    sample_end = tot_data_samples
                sample_start = int(odr * start_time)
                if sample_start >= tot_data_samples:
                    return ([],[])
                read_start_bytes = sample_start * dataframe_byte_size
                read_end_bytes = sample_end * dataframe_byte_size

            start_data_and_times_bytes_idx = start_data_bytes_idx + nof_timestamps_in_start * timestamp_byte_size
            nof_counter_in_start = math.floor(start_data_and_times_bytes_idx/data_packet_size)
            start_idx = start_data_and_times_bytes_idx + nof_counter_in_start * data_protocol_size
            
            acq_info = self.get_acquisition_info()
            acq_start_time = parser.isoparse(acq_info['start_time'])
            acq_end_time = parser.isoparse(acq_info['end_time'])
            acquisition_duration = (acq_end_time - acq_start_time).total_seconds()
            
            # Last available timestamp
            last_timestamp = acquisition_duration
            
            if end_time == -1 or end_time > last_timestamp:
                end_time = last_timestamp

            if start_time > last_timestamp:
                return [],[]
            
            byte_chest_index = 0
            raw_data_array_index = 0
            last_index = 0
            prev_timestamp = None
            nof_prev_timestamps = 0
            
            def __extract_data(start_time, end_time, nof_prev_timestamps):
                # Preallocate the byte_chest and raw_data_array with estimated sizes to avoid repeated reallocation
                estimated_size = (nof_data_packet +1) * (cmplt_pkt_size - data_protocol_size)
                byte_chest = np.empty(estimated_size, dtype='uint8')
                raw_data_array = np.empty(estimated_size, dtype='uint8')
                
                last_index = comp_status.get("last_index", 0)
                missing_bytes = comp_status.get("missing_bytes", 0)
                saved_bytes = comp_status.get("saved_bytes", 0)
                
                if last_index == 0 and start_idx != 0:
                    data_and_ts = nof_prev_timestamps * (dataframe_byte_size + timestamp_byte_size)
                    nof_counters = math.ceil(data_and_ts/data_packet_size)                    
                    packet_bytes = (nof_counters * data_protocol_size) + (nof_prev_timestamps * (dataframe_byte_size + timestamp_byte_size))
                    missing_bytes = math.ceil(packet_bytes/cmplt_pkt_size)*cmplt_pkt_size - packet_bytes
                    log.debug(f"User customized time boudaries: {start_time}, {end_time}")
                    log.debug(f"- packet_bytes: {packet_bytes}")
                    log.debug(f"- missing_bytes: {missing_bytes}")
                    last_index = packet_bytes

                byte_chest_index = 0
                raw_data_array_index = 0
                data_byte_counter = 0
                extracted_data_length = 0
                end_time_flag = False
                skip_counter_check = False
                prev_timestamp = None

                with open(file_path, 'rb') as f:
                    for n in range(nof_data_packet+1):
                        file_index = last_index + (n * cmplt_pkt_size)
                        log.debug(f"missing_bytes: {missing_bytes}")
                        log.debug(f"file_index: {file_index}")
                        if (file_index >= file_size):
                            comp_status["missing_bytes"] = byte_chest_index
                            comp_status["saved_bytes"] = raw_data_array_index
                            comp_status["last_index"] = file_index
                            break # EOF - No enough (cmplt_pkt_size) data to read! Extraction algorithm ends here
                        f.seek(file_index)
                        if last_index != 0:
                            if saved_bytes != 0 and saved_bytes <= missing_bytes:
                                raw_data = f.read(missing_bytes)
                                log.debug(f"Bytes read from file: {missing_bytes}")
                                comp_status["is_same_dps"] = True
                                if len(raw_data) < missing_bytes:
                                    return [],None
                                data_bytes = raw_data[:missing_bytes]
                                counter_bytes = []
                                skip_counter_check = True
                            else:
                                raw_data = f.read(missing_bytes + cmplt_pkt_size)
                                comp_status["is_same_dps"] = False
                                log.debug(f"Bytes read from file: {missing_bytes + cmplt_pkt_size}")
                                if len(raw_data) < missing_bytes + cmplt_pkt_size:
                                    return [],None
                                data_bytes = raw_data[:missing_bytes] + raw_data[missing_bytes + data_protocol_size:]
                                counter_bytes = raw_data[missing_bytes:missing_bytes+data_protocol_size]
                            last_index += missing_bytes
                            comp_status["missing_bytes"] = missing_bytes = 0
                        else:
                            raw_data = f.read(cmplt_pkt_size)
                            log.debug(f"Bytes read from file: {cmplt_pkt_size}")
                            if (len(raw_data) + byte_chest_index) < cmplt_pkt_size:
                                return [],None
                            
                            data_bytes = raw_data[data_protocol_size:]
                            counter_bytes = raw_data[:data_protocol_size]
                        
                        if not skip_counter_check:
                            counter = struct.unpack('<I', counter_bytes)[0]
                            log.debug(f"Extracted counter: {counter}")
                            log.debug(f"data_byte_counter: {data_byte_counter}")
                            data_byte_counter = comp_status.get("prev_data_byte_counter")
                            if data_byte_counter is None:
                                comp_status["prev_data_byte_counter"] =  counter
                            else:
                                if (counter - data_byte_counter) != data_packet_size:
                                    is_first_chunk = comp_status.get("is_first_chunk", True)
                                    if is_first_chunk and n == 0 and data_byte_counter == 0:
                                        #drop the first complete packet (data_packet_size) and go ahead with the data extraction and validation
                                        log.warning(f"Counter mismatch at the beginning of the file: {counter} != {data_byte_counter + data_packet_size}")
                                        log.warning(f"Skipping the first packet and continuing with the data extraction")
                                        continue
                                    else:
                                        raise DataCorruptedException(file_path)
                                comp_status["prev_data_byte_counter"] = counter

                        # Directly copy data into preallocated array
                        data_bytes_length = len(data_bytes)
                        byte_chest[byte_chest_index:byte_chest_index+data_bytes_length] = np.frombuffer(data_bytes, dtype='uint8')
                        byte_chest_index += data_bytes_length

                        if timestamp_byte_size == 0:
                            if byte_chest_index >= read_end_bytes - read_start_bytes:
                                extracted_data_length = read_end_bytes - read_start_bytes
                                raw_data_array[:extracted_data_length] = byte_chest[:extracted_data_length]
                                raw_data_array_index += extracted_data_length
                                end_time_flag = True
                                
                                is_same_data_packet = comp_status.get("is_same_dps")
                                if is_same_data_packet:
                                    bytes_processed = last_index
                                    comp_status["is_same_dps"] = False
                                else:
                                    bytes_processed = (last_index + (n+1) * cmplt_pkt_size)

                                comp_status["missing_bytes"] = byte_chest_index - extracted_data_length
                                comp_status["saved_bytes"] = raw_data_array_index
                                comp_status["last_index"] = bytes_processed - comp_status["missing_bytes"]
                                byte_chest_index -= extracted_data_length
                                break
                        else:
                            extracted_timestamp = None
                            while byte_chest_index >= dataframe_byte_size + timestamp_byte_size:
                                extracted_timestamp_bytes = byte_chest[dataframe_byte_size:dataframe_byte_size+timestamp_byte_size]
                                extracted_timestamp = struct.unpack('d', extracted_timestamp_bytes)[0]
                                log.debug(f"start_time: {start_time}")
                                log.debug(f"extracted_timestamp: {extracted_timestamp}")
                                log.debug(f"end_time: {end_time}")
                                if extracted_timestamp > start_time:
                                    
                                    extracted_data_length = dataframe_byte_size + timestamp_byte_size
                                    raw_data_array[raw_data_array_index:raw_data_array_index+extracted_data_length] = byte_chest[:extracted_data_length]
                                    raw_data_array_index += extracted_data_length
                                    
                                    if end_time != -1 and extracted_timestamp >= end_time:
                                        if prev_timestamp is None and comp_status.get("is_first_chunk",False):
                                            prev_timestamp = extracted_timestamp
                                            log.debug(f"prev_timestamp: {prev_timestamp}")
                                            if prev_timestamp > start_time:
                                                if last_index == 0:
                                                    prev_timestamp = comp_status.get("ioffset",0)
                                                else:
                                                    end_time_flag = True
                                                    break

                                        end_time = extracted_timestamp
                                        end_time_flag = True
                                        
                                        is_same_data_packet = comp_status.get("is_same_dps")
                                        if is_same_data_packet:
                                            bytes_processed = last_index
                                            comp_status["is_same_dps"] = False
                                        else:
                                            bytes_processed = (last_index + (n+1) * cmplt_pkt_size)
                                        
                                        comp_status["missing_bytes"] = byte_chest_index - extracted_data_length
                                        comp_status["saved_bytes"] = raw_data_array_index
                                        comp_status["last_index"] = bytes_processed - comp_status["missing_bytes"]
                                        break
                                    else:
                                        if prev_timestamp is None and comp_status.get("is_first_chunk",False):
                                            prev_timestamp = extracted_timestamp
                                            log.debug(f"prev_timestamp: {prev_timestamp}")
                                            if prev_timestamp > start_time:
                                                if last_index == 0:
                                                    prev_timestamp = comp_status.get("ioffset",0)
                                                else:
                                                    end_time_flag = True
                                                    comp_status.pop("prev_data_byte_counter", None)
                                                    break
                                    
                                    byte_chest = byte_chest[extracted_data_length:]
                                    byte_chest_index -= extracted_data_length

                                else:
                                    is_first_chunk = comp_status.get("is_first_chunk", False)
                                    if is_first_chunk:
                                        prev_timestamp = extracted_timestamp
                                        log.debug(f"prev_timestamp: {prev_timestamp}")

                                    byte_chest = byte_chest[dataframe_byte_size + timestamp_byte_size:]
                                    byte_chest_index -= dataframe_byte_size + timestamp_byte_size
                                    break

                            if "last_index" not in comp_status and extracted_timestamp is not None and (last_timestamp - extracted_timestamp) < (s_samples_per_ts/odr):
                                end_time_flag = True
                                bytes_processed = (last_index + (n+1) * cmplt_pkt_size)                                    
                                comp_status["missing_bytes"] = byte_chest_index
                                comp_status["saved_bytes"] = raw_data_array_index
                                comp_status["last_index"] = bytes_processed - comp_status["missing_bytes"]
                                break

                            if last_index != 0 and extracted_timestamp is not None and extracted_timestamp < end_time and byte_chest_index != 0 and comp_status["missing_bytes"] != 0:
                                end_time_flag = True
                                comp_status["missing_bytes"] = byte_chest_index
                                comp_status["saved_bytes"] = raw_data_array_index
                                comp_status["last_index"] = last_index - comp_status["missing_bytes"]
                                break

                        if end_time_flag:
                            break

                # Trim the preallocated arrays to the actual size of the data
                byte_chest = byte_chest[:byte_chest_index]
                raw_data_array = raw_data_array[:raw_data_array_index]
                return raw_data_array, prev_timestamp
            
            nof_prev_timestamps = max(0, nof_timestamps_in_start - 2)
            raw_data_array, prev_timestamp = __extract_data(start_time, end_time, nof_prev_timestamps)

            if nof_prev_timestamps != 0:
                while prev_timestamp is not None and prev_timestamp > start_time:
                    nof_prev_timestamps -= 1
                    raw_data_array, prev_timestamp = __extract_data(start_time, end_time, nof_prev_timestamps)

            log.debug("Data & Timestamp extraction algorithm COMPLETED!")
            data, timestamp = self.__process_datalog(comp_name, comp_status, raw_data_array,
                                                    dataframe_byte_size, timestamp_byte_size,
                                                    raw_flag, start_time, prev_timestamp)
            
            if "last_index" not in comp_status:
                bytes_processed = (last_index + (nof_data_packet+1) * cmplt_pkt_size)
                comp_status["missing_bytes"] = byte_chest_index
                comp_status["saved_bytes"] = raw_data_array_index
                comp_status["last_index"] = bytes_processed - comp_status["missing_bytes"]

            #DEBUG
            log.debug(f"data Len: {len(data)}")
            log.debug(f"Time Len: {len(timestamp)}")
            return data, timestamp
        
        elif c_type == ComponentTypeEnum.ACTUATOR.value and "odr" not in comp_status:
            if comp_name == MC_SLOW_TELEMETRY_COMP_NAME or comp_name == MC_FAST_TELEMETRY_COMP_NAME:
                if data_packet_size is not None:
                    with open(file_path, "rb") as f:
                        f_data = f.read()
                        if not f_data:
                            log.error("No data @ index: {} for file \"{}\" size: {}[bytes]".format(0, file_path, os.stat(f.name).st_size))
                            raise NoDataAtIndexError(0, file_path, os.stat(f.name).st_size)
                        raw_data = np.fromstring(f_data, dtype='uint8')
                        new_array = self.remove_4bytes_every_n_optimized(raw_data, cmplt_pkt_size)
                    
                    #NOTE: The following value should be obtained from:
                    # -SLOW MC TELEMETRIES: "n_of_enabled_slow_telemetries * data_type (bytes_size)"
                    # -FAST MC TELEMETRIES: "dim * data_type (bytes_size)"
                    data_packet_size = comp_status.get("usb_dps")
                    if data_packet_size is not None:
                        timestamp_byte_size = 8
                        dataframe_byte_size = data_packet_size - timestamp_byte_size

                    data, timestamp = self.__process_datalog(comp_name, comp_status, new_array, dataframe_byte_size, timestamp_byte_size, raw_flag = raw_flag )

                    #DEBUG
                    log.debug(f"data Len: {len(data)}")
                    log.debug(f"Time Len: {len(timestamp)}")
                    return data, timestamp
                else:
                    log.error("Actuator type not supported")
                    return None, None

        elif c_type == ComponentTypeEnum.ALGORITHM.value:
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:#"fft":
                log.debug("FFT Algorithm! No batch")
                
                dataframe_byte_size = int(s_dim * s_data_type_len)
                timestamp_byte_size = 0

                with open(file_path, "rb") as f:
                    f_data = f.read()
                    if not f_data:
                        log.error("No data @ index: {} for file \"{}\" size: {}[bytes]".format(0, file_path, os.stat(f.name).st_size))
                        raise NoDataAtIndexError(0, file_path, os.stat(f.name).st_size)
                    raw_data = np.fromstring(f_data, dtype='uint8')
                    new_array = self.remove_4bytes_every_n_optimized(raw_data, cmplt_pkt_size)
                
                data, timestamp = self.__process_datalog(comp_name, comp_status, new_array, dataframe_byte_size, timestamp_byte_size, raw_flag = raw_flag, start_time=start_time)

                #DEBUG
                log.debug(f"data Len: {len(data)}")
                log.debug(f"Time Len: {len(timestamp)}")
                return data, timestamp
            else:
                log.error("Algorithm type not supported")
                return None, None


    #TODO! DEPRECATE OR REMOVE THIS FUNCTION
    def get_data_and_timestamps(self, sensor_name, sub_sensor_type, start_time = 0, end_time = -1, raw_flag = False):
        # get sensor component status
        s_stat = self.__get_sensor_status(sensor_name)
        
        # get acquisition interface
        interface = self.acq_info_model['interface']
        
        # data protocol size:
        data_protocol_size = 4

        data_packet_size = 0
        # data packet size (0:sd card, 1:usb, 2:ble, 3:serial)
        if interface == 0:
            data_packet_size = s_stat["sd_dps"] - data_protocol_size
        elif interface == 1:
            data_packet_size = s_stat["usb_dps"]
        elif interface == 2:
            data_packet_size = s_stat["ble_dps"]
        elif interface == 3:
            data_packet_size = s_stat["serial_dps"]
        else:
            log.error(f"Unknown interface: {interface}. check your device_config.json file")
            raise
        
        # get dat file path and size (obtained from "sensor_name + sub_sensor_type")
        file_path = self.__get_sensor_file_path(sensor_name)
        file_size = os.path.getsize(file_path)
        
        cmplt_pkt_size = data_packet_size + data_protocol_size
        nof_data_packet = file_size // cmplt_pkt_size # "//" math.floor equivalent
        checked_file_path = os.path.splitext(os.path.abspath(file_path))[0] + "_checked.dat"

        #TODO: Check data integrity looking at the first 4 bytes counter
        with open(checked_file_path, 'wb') as f, open(file_path, "rb") as rf:
            # cmplt_pkt_size = data_packet_size + data_protocol_size
            for n in range(nof_data_packet):
                index = n * cmplt_pkt_size
                rf.seek(index)
                rf_data = rf.read(cmplt_pkt_size)[4:]
                if not rf_data:
                    log.error("No data @ index: {} for file \"{}\" size: {}[bytes]".format(index, file_path, os.stat(f.name).st_size))
                    raise NoDataAtIndexError(index, file_path, os.stat(f.name).st_size)
                f.write(np.frombuffer(rf_data, dtype='uint8'))

        # get checked dat file path and size (obtained from "sensor_name + sub_sensor_type") reusing the same variables
        file_path = self.__get_checked_sensor_file_path(sensor_name)
        file_size = os.path.getsize(file_path)
        
        c_type = s_stat.get("c_type")

        if c_type == ComponentTypeEnum.ALGORITHM.value:
            algo_type = s_stat.get("algorithm_type")
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                # get FFT algo "dimensions" --> FFT Length
                s_dim = s_stat.get("fft_length")
            else:
                s_dim = s_stat.get('dim')
        else:
            # get sensor dimensions
            s_dim = s_stat.get('dim', 1)
        
        # get Data type byte length
        s_data_type_len = TypeConversion.check_type_length(s_stat['data_type'])
        
        # get samples per ts
        spts = s_stat.get('samples_per_ts', {})
        if isinstance(spts, int):
            s_samples_per_ts = spts
        else:
            s_samples_per_ts = spts.get('val', 0)
        
        if c_type == ComponentTypeEnum.SENSOR.value:
            #TODO sample_end = N/s = 26667 in 1 sec, 266670 in 10 sec, --> 26667*10 in 1*10 sec --> ODR*end_time(in sec) = sample_end
            #TODO sample_start = N/s = 104 in 1 sec, 1040 in 10 sec, --> 104*10 in 1*10 sec --> ODR*start_time(in sec) = sample_start
            s_category = s_stat.get("sensor_category")
            if s_category is not None and s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                odr = 1/(s_stat.get("intermeasurement_time")/1000) if s_stat.get("intermeasurement_time") > s_stat.get("exposure_time")/1000 + 6  else (1/((s_stat.get("exposure_time")/1000 + 6)/1000))
            elif s_category is not None and s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value:
                odr = 1/(s_stat.get("adc_conversion_time")/1000000)
            else:
                odr = s_stat.get("measodr")
                if odr is None or odr == 0:
                    odr = s_stat.get("odr",1)
            sample_end = int(odr * end_time) if end_time != -1 else -1
            total_samples = file_size//(s_data_type_len * s_dim)
            if sample_end > total_samples:
                sample_end = total_samples
            sample_start = int(odr * start_time)

            #SAMPLES_PER_TS check
            if s_samples_per_ts > total_samples:
                s_samples_per_ts = 0
            try:
                # Sample per Ts == 0 #######################################################################           
                if s_samples_per_ts == 0:
                    if sample_end == -1:
                        sample_end = total_samples
                    
                    read_start_bytes = sample_start * (s_dim * s_data_type_len)
                    read_end_bytes = sample_end * (s_dim * s_data_type_len)#dataframe_byte_size

                    dataframe_byte_size = read_end_bytes - read_start_bytes
                    timestamp_byte_size = 0
                    blocks_before_ss = 0

                # Sample per Ts != 0 #######################################################################
                else:
                    dataframe_byte_size = s_samples_per_ts * s_dim * s_data_type_len
                    timestamp_byte_size = 8

                    if sample_end == -1:
                        n_of_blocks_in_file = file_size // (timestamp_byte_size + dataframe_byte_size)
                        sample_end = n_of_blocks_in_file * s_samples_per_ts
                    
                    blocks_before_ss = sample_start // s_samples_per_ts
                    blocks_before_se = sample_end // s_samples_per_ts

                    read_start_bytes = (blocks_before_ss * dataframe_byte_size) + ((blocks_before_ss - 1) * timestamp_byte_size) if blocks_before_ss > 0 else 0
                    read_end_bytes = ((blocks_before_se + 1) * dataframe_byte_size) + ((blocks_before_se + 1) * timestamp_byte_size)
                
                with open(file_path, "rb") as f:
                    f.seek(read_start_bytes)
                    raw_data = f.read(read_end_bytes - read_start_bytes)
                    if len(raw_data) == 0:
                        log.error("No data @ index: {} for file \"{}\" size: {}[bytes]".format(read_start_bytes, file_path, os.stat(f.name).st_size))
                        raise NoDataAtIndexError(read_start_bytes, file_path, os.stat(f.name).st_size)
                
                raw_data = np.fromstring(raw_data, dtype='uint8')

                # if the start_sample isn't in the first block (pre_t_bytes_id != 0)
                if read_start_bytes != 0 :
                    first_timestamp = raw_data[:timestamp_byte_size] if s_samples_per_ts != 0 else 0
                    s_stat['ioffset'] = np.frombuffer(first_timestamp, dtype='double') if s_samples_per_ts != 0 else 0
                    #remove the first timestamp
                    raw_data = raw_data[timestamp_byte_size:]

                data, timestamp = self.__process_datalog(sensor_name, s_stat, raw_data,
                                                         dataframe_byte_size, timestamp_byte_size,
                                                         raw_flag, start_time)

                #DEBUG
                log.debug(f"data Len: {len(data)}")
                log.debug(f"Time Len: {len(timestamp)}")

                os.remove(file_path)

                return data, timestamp

            except MemoryError:
                log.error("Memory Error occoured! You should batch process your {} file".format(file_path))
                os.remove(file_path)
                raise
            except OverflowError:
                log.error("Memory Error occoured! You should batch process your {} file".format(file_path))
                os.remove(file_path)
                raise

        elif c_type == ComponentTypeEnum.ALGORITHM.value:
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:#"fft":
                log.info("FFT Algorithm!")
                
                dataframe_byte_size = int(s_dim * s_data_type_len)
                timestamp_byte_size = 0

                with open(file_path, "rb") as f:
                    f_data = f.read()
                    if not f_data:
                        log.error("No data @ index: {} for file \"{}\" size: {}[bytes]".format(0, file_path, os.stat(f.name).st_size))
                        raise NoDataAtIndexError(0, file_path, os.stat(f.name).st_size)
                    raw_data = np.fromstring(f_data, dtype='uint8')
                
                data, timestamp = self.__process_datalog(sensor_name, s_stat, raw_data, dataframe_byte_size, timestamp_byte_size, raw_flag = raw_flag )

                #DEBUG
                log.debug(f"data Len: {len(data)}")
                log.debug(f"Time Len: {len(timestamp)}")
                os.remove(file_path)
                return data, timestamp
            else:
                log.error("Algorithm type not supported")
                os.remove(file_path)
                return None, None

        elif c_type == ComponentTypeEnum.ACTUATOR.value:
            # s_samples_per_ts = 1
            
            if sensor_name == MC_SLOW_TELEMETRY_COMP_NAME or sensor_name == MC_FAST_TELEMETRY_COMP_NAME:#"slow_mc_telemetries":
                if data_packet_size is not None:
                    timestamp_byte_size = 8
                    dataframe_byte_size = data_packet_size - timestamp_byte_size

                    # n_of_samples = sample_end - sample_start
                    # blocks_before_ss = 0

                    # if sample_end == -1:
                    n_of_samples = int(file_size/dataframe_byte_size)
                    # sample_end = n_of_samples
                    
                    # read_start_bytes = sample_start * (s_data_type_len* s_dim)
                    # read_end_bytes = sample_end * (s_data_type_len* s_dim)
                    with open(file_path, "rb") as f:
                        # f.seek(read_start_bytes)
                        # f_data = f.read(read_end_bytes - read_start_bytes)
                        f_data = f.read()
                        if not f_data:
                            log.error("No data @ index: {} for file \"{}\" size: {}[bytes]".format(0, file_path, os.stat(f.name).st_size))
                            raise NoDataAtIndexError(0, file_path, os.stat(f.name).st_size)
                        raw_data = np.fromstring(f_data, dtype='uint8')

                    # print(len(raw_data))
                    # print(dataframe_byte_size)
                    if n_of_samples >= 1:
                        first_timestamp = raw_data[dataframe_byte_size:dataframe_byte_size + timestamp_byte_size]
                        # print(struct.unpack("=d",first_timestamp))
                    
                    data, timestamp = self.__process_datalog(sensor_name, s_stat, raw_data, dataframe_byte_size, timestamp_byte_size, raw_flag = raw_flag )

                    #DEBUG
                    log.debug("data Len: {}".format(len(data)))
                    log.debug("Time Len: {}".format(len(timestamp)))
                    os.remove(file_path)
                    return data, timestamp
                else:
                    log.error("Actuator type not supported")
                    os.remove(file_path)
                    return None, None
                    # raise

    def get_ispu_output_column_names(self):
        if self.ispu_output_format is not None:
            return [o["name"] for o in self.ispu_output_format["output"]]
        
    def get_ispu_output_types(self):
        if self.ispu_output_format is not None:
            return [TypeConversion.check_type(o["type"]) for o in self.ispu_output_format["output"]]
        else:
            return None

    def __get_mems_columns_names(self, ss_stat, sensor_name, s_type, numAxes):
        if not (s_type == "mlc" or s_type == "stredl" or s_type == "ispu"):
            cc = ['x', 'y', 'z'] if numAxes == 3 else ['x', 'y'] if numAxes == 2 else []
            col_prefix = s_type[0].upper() + '_' if cc else ""
            col_postfix = ''
            if "unit" in ss_stat:
                unit = ss_stat["unit"]
                col_postfix = ' [' + UnitMap().unit_dict.get(unit, unit) + ']'
            c = [col_prefix + s + col_postfix for s in cc] if cc else [s_type.upper() + col_postfix]
        else:
            if s_type == "ispu":
                c = self.get_ispu_output_column_names()
            else: 
                if numAxes > 0:
                    cc = range(ss_stat['dim'])
                    col_prefix = s_type[0].upper() + '_'
                    c = [col_prefix + str(s) for s in cc]
                else:
                    log.error("Wrong number of sensor axes ({})".format(numAxes))
                    raise NSensorAxesError(sensor_name)
        return c

    # Function to find the nearest index for a given time in the times array
    def find_nearest_index(self, array, value):
        array = np.squeeze(array)  # Remove single-dimensional entries
        idx = (np.abs(array - value)).argmin()
        return idx

    def get_sensor_axis_label(self, ss_stat, sensor_name):
        s_type = ""
        c_type = ss_stat.get("c_type")
        if c_type == ComponentTypeEnum.SENSOR.value:
            numAxes = int(ss_stat.get('dim',1))
            _, s_type = FileManager.decode_file_name(sensor_name)
            s_category = ss_stat.get("sensor_category")
            if s_category is not None:
                al = []
                if s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                    al = ["Red","Visible","Blue","Green","IR","Clear"]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_PRESENCE.value:
                    al = ["Tambient (raw)","Tobject (raw)","Tobject (emb_comp)","Tpresence",
                            "Presence flag","Tmotion","Motion flag","Tobject (sw_comp)",
                            "Tobject_change (sw_comp)","Motion flag (sw_comp)","Presence flag (sw_comp)"]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_RANGING.value:
                    if ss_stat.get('output_format'):
                        resolution = ss_stat.get("resolution")
                        if resolution is not None:
                            res = int(resolution.split("x")[0])
                        for i in range(res*res):
                            al += [f"Target Status Z{i}",f"Distance Z{i}"]
                    else: #NOTE: Code for old firmware versions
                        res = 4 if ss_stat['dim'] == 128 else 8
                        for i in range(res*res):
                            al += [f"N Target Z{i}",f"Ambient per SPAD Z{i}",f"Signal per SPAD Z{i}",
                                f"Target Status Z{i}",f"Distance Z{i}",f"Signal per SPAD Z{i}",
                                f"Target Status Z{i}",f"Distance Z{i}"]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_CAMERA:
                    raise UnsupportedSensorCategoryError(sensor_name)#TODO
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value:
                    al = ["Voltage [mV]","Voltage(VShunt) [mV]","Current [A]","Power [mW]"]     
                else:
                    if not (s_type == "mlc" or s_type == "stredl" or s_type == "ispu"):
                        al = ['x', 'y', 'z'] if numAxes == 3 else ['x', 'y'] if numAxes == 2 else ['x']
                    else:
                        if s_type == "ispu":
                            al = self.get_ispu_output_column_names()
                        else: 
                            if numAxes > 0:
                                cc = range(ss_stat['dim'])
                                col_prefix = 'reg_'
                                al = [col_prefix + str(s) for s in cc]
                return al
            else:
                cc = range(ss_stat['dim'])
                col_prefix = 'reg_'
                return [col_prefix + str(s) for s in cc]
        else:
            return None

    def get_component_columns_names(self, ss_stat, sensor_name):
        s_type = ""
        # d_type = ss_stat.get("data_type")
        c_type = ss_stat.get("c_type")
        if c_type == ComponentTypeEnum.SENSOR.value:
            numAxes = int(ss_stat.get('dim',1))
            s_name, s_type = FileManager.decode_file_name(sensor_name)
            s_category = ss_stat.get("sensor_category")
            if s_category is not None:
                if s_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                    c = ["Red","Visible","Blue","Green","IR","Clear"]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_PRESENCE.value:
                    c = ["Tambient (raw)","Tobject (raw)","Tobject (emb_comp)","Tpresence",
                            "Presence flag","Tmotion","Motion flag","Tobject (sw_comp)",
                            "Tobject_change (sw_comp)","Motion flag (sw_comp)","Presence flag (sw_comp)"]
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_RANGING.value:
                    if ss_stat.get('output_format'):
                        resolution = ss_stat.get("resolution")
                        if resolution is not None:
                            res = int(resolution.split("x")[0])
                        c = []
                        # for i in range(res):
                        #     for j in range(res):
                        #         c += [f"Target Status T1_Z({i},{j})",f"Distance T1_Z({i},{j})"]
                        for i in range(res*res):
                            c += [f"Target Status Z{i}",f"Distance Z{i}"]
                    else: #NOTE: Code for old firmware versions
                        res = 4 if ss_stat['dim'] == 128 else 8
                        c = []
                        # for i in range(res):
                        #     for j in range(res):
                        #         c += [f"N Target Z({i},{j})",f"Ambient per SPAD Z({i},{j})",f"Signal per SPAD T1_Z({i},{j})",
                        #             f"Target Status T1_Z({i},{j})",f"Distance T1_Z({i},{j})",f"Signal per SPAD T2_Z({i},{j})",
                        #             f"Target Status T2_Z({i},{j})",f"Distance T2_Z({i},{j})"]
                        for i in range(res*res):
                            c += [f"N Target Z{i}",f"Ambient per SPAD Z{i}",f"Signal per SPAD Z{i}",
                                f"Target Status Z{i}",f"Distance Z{i}",f"Signal per SPAD Z{i}",
                                f"Target Status Z{i}",f"Distance Z{i}"]
                        
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_CAMERA:
                    raise UnsupportedSensorCategoryError(sensor_name)#TODO
                elif s_category == SensorCategoryEnum.ISENSOR_CLASS_POWERMETER.value:
                    c = ["Voltage [mV]","Voltage(VShunt) [mV]","Current [A]","Power [mW]"]     
                else:
                    c = self.__get_mems_columns_names(ss_stat, sensor_name, s_type, numAxes)
            else:
                c = self.__get_mems_columns_names(ss_stat, sensor_name, s_type, numAxes)

        elif c_type == ComponentTypeEnum.ALGORITHM.value:
            algo_type = ss_stat.get("algorithm_type")
            if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                cc = range(ss_stat['fft_length'])
            else:
                cc = range(ss_stat['dim'])
            col_prefix = sensor_name.upper() + '_'
            c = [col_prefix + str(s) for s in cc]
        
        elif c_type == ComponentTypeEnum.ACTUATOR.value:
            telemetries_keys = self.__get_active_mc_telemetries_names(ss_stat, sensor_name)
            cc = range(len(telemetries_keys))
            c = [tk.upper() for tk in telemetries_keys]
        
        return c

    def __to_dataframe(self, data, time, ss_stat, sensor_name, labeled = False, which_tags:list = [], raw_flag = False):
        if data is not None and time is not None:
            cols = []
            s_type = ""
            c_type = ss_stat.get("c_type")
            if c_type == ComponentTypeEnum.SENSOR.value:
                s_name, s_type = FileManager.decode_file_name(sensor_name)
            
            if s_type != "ispu":
                try:
                    if len(time) > len(data):
                        time = time[:len(data)]
                    val = np.concatenate((time, data), axis=1)
                except:
                    pass
            else:
                ispu_out_types = self.get_ispu_output_types()
                if ispu_out_types is not None:
                    output_bytes_count = sum(TypeConversion.check_type_length(ot) for ot in ispu_out_types)
                    unpack_str = "=" + ''.join(TypeConversion.get_format_char(ot) for ot in ispu_out_types)
                    output_data = [struct.unpack(unpack_str, np.array(d[0:output_bytes_count])) for d in data]
                    np_output_data = np.array(output_data)
                    final_out_data = [np_output_data[:,i] for i in range(len(ispu_out_types))]
                    final_out_data = np.transpose(final_out_data)
                    val = np.array(time)
                    val = np.append(val, final_out_data, axis=1)
                else:
                    raise MissingISPUOutputDescriptorException(sensor_name)
            
            cols =np.concatenate((["Time"], self.get_component_columns_names(ss_stat, sensor_name)), axis=0)

            try:
                ss_data_frame = pd.DataFrame(data=val, columns=cols)
            except Exception as e:
                pass

            if labeled:
                tags = self.get_tags()
                if len(tags) == 0:
                    raise MissingTagsException() 
                if len(which_tags) > 0:
                    filtered_tags = [t for t in tags if t["label"] in which_tags]
                    tags = filtered_tags

                for tag in tags:
                    tag_label = tag.get("label")
                    tag_times = tag.get("times")
                    for t in tag_times:
                        enter_time = t[0]
                        exit_time = t[1]
                        # Find the nearest indices for the enter and exit times
                        enter_index = self.find_nearest_index(time, enter_time)
                        exit_index = self.find_nearest_index(time, exit_time)
                        
                        # Create an array of booleans with the same length as times_array
                        bool_array = np.zeros_like(time, dtype=bool)
                        if enter_time <= time[-1]:
                            if not(exit_time <= time[-1] and (enter_index == exit_index)):
                                # Set True for indices between enter_index and exit_index (inclusive)
                                bool_array[enter_index:exit_index+1] = True

                        # Flatten the boolean array to match the shape of the input times array
                        bool_array = bool_array.flatten()
                        
                        if tag_label not in ss_data_frame:
                            ss_data_frame[tag_label] = bool_array
                        else:
                            ss_data_frame[tag_label] = ss_data_frame[tag_label] | bool_array

            sensitivity = ss_stat.get("sensitivity", 1)
            if c_type == ComponentTypeEnum.ACTUATOR.value:
                sensitivity = 0
            data_type = ss_stat.get("data_type")
            ss_data_frame["Time"] = ss_data_frame["Time"].round(decimals=6)
            for i, c in enumerate(cols):
                if c != "Time":
                    if raw_flag or sensitivity == 1 and data_type:
                        if i != 0:
                            ss_data_frame[c] = ss_data_frame[c].astype(TypeConversion.get_np_dtype(data_type))
                            if data_type in ["float","float32","double"]:
                                ss_data_frame[c] = ss_data_frame[c].round(decimals=6)
                    else:
                        ss_data_frame[c] = ss_data_frame[c].round(decimals=6)
            
            return ss_data_frame
        log.error("Error extracting data and timestamp from sensor {}".format(sensor_name))
        raise DataExtractionError(sensor_name)

    #TODO deprecate this function
    def get_dataframe(self, sensor_name, sensor_type = None, start_time = 0, end_time = -1, labeled = False, raw_flag = False):       
        # get sensor component status
        s_stat = self.__get_sensor_status(sensor_name)
        
        res = self.get_data_and_timestamps(sensor_name, sensor_type, start_time, end_time, raw_flag)
        if res is not None:
            data, time = res
            # data, time, ss_stat, sensor_name, labeled = False, which_tags:list = [], raw_flag = False
            return self.__to_dataframe(data, time, s_stat, sensor_name, labeled, [], raw_flag)
        log.error("Error extracting data and timestamps from {} sensor .dat file".format(sensor_name))
        raise DataExtractionError(sensor_name)
    
    def get_dataframe_batch(self, comp_name, comp_status, start_time = 0, end_time = -1, labeled = False, raw_flag = False, which_tags:list = []):
        res = self.get_data_and_timestamps_batch(comp_name, comp_status, start_time, end_time, raw_flag)
        if res[0] is not None and res[1] is not None:
            data, time = res
            if len(data) > 0:
                return self.__to_dataframe(data, time, comp_status, comp_name, labeled, which_tags, raw_flag)
        return None

    def get_dat_file_list(self):
        """
        Retrieves a list of .dat files from the acquisition folder associated with this HSDatalog instance.
        The method uses the FileManager class to search for .dat files within the acquisition folder path.

        :return: A list of .dat file paths.
        """
        # Use the FileManager class to get a list of .dat files from the acquisition folder.
        # The '__acq_folder_path' is an instance attribute that stores the path to the acquisition folder.
        return FileManager.get_dat_files_from_folder(self.__acq_folder_path)


    # #======================================================================================#
    ### OFFLINE Plots  #######################################################################
    #========================================================================================#

    # Plots Helper Functions ################################################################################################################
    def __plot_ranging_sensor(self, sensor_name, ss_data_frame, res, output_format):

        # Function to extract the target identifier from the key
        def __extract_target_identifier(key):
            if key != "nof_outputs":
                return "".join(key.split("_")[:-1])
            return None                

        # Group the targets
        targets = {}
        new_shape = (res, res)
        
        # Create a list to store figures to be returned
        figures = []

        nof_outputs = output_format.get("nof_outputs")
        for key, value in output_format.items():
            identifier = __extract_target_identifier(key)
            if identifier is not None:
                # Initialize the target group if it doesn't exist
                if identifier not in targets:
                    targets[identifier] = {'status': None, 'distance': None}
                # Assign the status or distance to the target group
                if 'status' in key:
                    targets[identifier]['status'] = value
                elif 'distance' in key:
                    targets[identifier]['distance'] = value
        
        for t in targets:
            dist_id = targets[t]['distance']["start_id"] + 1
            dist_df = ss_data_frame.iloc[:, range(dist_id, len(ss_data_frame.columns), nof_outputs)]
            status_id = targets[t]['status']["start_id"] + 1
            status_df = ss_data_frame.iloc[:, range(status_id, len(ss_data_frame.columns), nof_outputs)]
            status_df.columns = dist_df.columns
            mask_df = status_df == 5
            times = ss_data_frame["Time"]
            # use the boolean matrix as a mask for the second dataframe
            masked_df = dist_df.where(mask_df)
            nof_rows = len(mask_df)

            dist_matrices = np.empty((nof_rows, ), dtype=object)
            for r_id in range(nof_rows):
                row_t1 = masked_df.iloc[r_id]
                t1_mat = np.array(row_t1.values).reshape(new_shape).astype('float')
                t1_mat = np.rot90(t1_mat, k=3)
                t1_mat = np.flip(t1_mat, axis=0)
                t1_mat = np.swapaxes(t1_mat, 0, 1)
                dist_matrices[r_id] = t1_mat

            # Handle NaN values by filling them with a default value (e.g., 0)
            dist_matrices = [np.nan_to_num(matrix, nan=-1) for matrix in dist_matrices]

            # Prepare annotation arrays for each frame
            annotations_list = []
            for frame_idx in range(nof_rows):
                frame_annotations = np.zeros((new_shape[0], new_shape[1]), dtype=object)
                for i in range(new_shape[0]):
                    for j in range(new_shape[1]):
                        z_index = new_shape[0]*(new_shape[0]-1-i)+(new_shape[1]-1-j)
                        z_value = dist_matrices[frame_idx][i][j]
                        if z_value == -1:
                            z_str = "-"
                        else:
                            z_str = f"{int(z_value)}"
                        frame_annotations[i][j] = f'Z{z_index}<br>{z_str}'
                annotations_list.append(frame_annotations)

            # Define a custom colorscale
            custom_colorscale = [
                [0, 'red'],  # Map -1 to red
                [0.001, 'rgb(68, 1, 84)'],  # Start of Viridis colorscale
                [0.125, 'rgb(72, 35, 116)'],
                [0.25, 'rgb(64, 67, 135)'],
                [0.375, 'rgb(52, 94, 141)'],
                [0.5, 'rgb(41, 120, 142)'],
                [0.625, 'rgb(32, 144, 140)'],
                [0.75, 'rgb(34, 167, 132)'],
                [0.875, 'rgb(68, 190, 112)'],
                [1, 'rgb(253, 231, 37)']  # End of Viridis colorscale
            ]

            # Create frames for animation
            frames = [
                go.Frame(
                    data=[go.Heatmap(
                        z=dist_matrices[i],
                        colorscale=custom_colorscale,
                        zmin=-1, zmax=4000,
                        text=annotations_list[i],
                        texttemplate="%{text}"
                    )],
                    name=str(i)
                )
                for i in range(nof_rows)
            ]

            # Create the initial heatmap with the first frame's annotations
            fig = go.Figure(
                data=[go.Heatmap(
                    z=dist_matrices[0],
                    colorscale=custom_colorscale,
                    zmin=-1, zmax=4000,
                    text=annotations_list[0],
                    texttemplate="%{text}"
                )],
                frames=frames,
                layout=go.Layout(
                    title=f"{sensor_name.upper()} - Target {t}",
                    xaxis=dict(
                        scaleanchor="y",
                        scaleratio=1
                    ),
                    yaxis=dict(
                        scaleratio=1
                    ),
                    updatemenus=[{
                        "type": "buttons",
                        "direction": "right",
                        "buttons": [
                            {
                                "label": "Play",
                                "method": "animate",
                                "args": [None, {
                                    "frame": {"duration": 500, "redraw": True},
                                    "fromcurrent": True
                                }]
                            },
                            {
                                "label": "Pause",
                                "method": "animate",
                                "args": [[None], {
                                    "frame": {"duration": 0, "redraw": True},
                                    "mode": "immediate"
                                }]
                            }
                        ],
                        "showactive": True,
                        "x": 1,
                        "y": -0.15,
                        "xanchor": "right",
                        "yanchor": "bottom"
                    }],
                    sliders=[{
                        "steps": [
                            {
                                "args": [[str(i)], {"frame": {"duration": 500, "redraw": True}, "mode": "immediate"}],
                                "label": f"{times[i]:.2f} s",
                                "method": "animate"
                            } for i in range(nof_rows)
                        ],
                        "transition": {"duration": 300},
                        "x": 0,
                        "y": -0.1,
                        "currentvalue": {"prefix": "Time: "},
                        "len": 1
                    }]
                )
            )

            # Add legend for invalid zones
            fig.add_trace(go.Scatter(
                x=[None], y=[None],
                mode='markers',
                marker=dict(size=10, color='red', symbol='square'),
                legendgroup='Invalid Zones',
                showlegend=True,
                orientation='h',
                name='Invalid Zones'
            ))

            # Update layout to place the legend above the graph and make it unclickable
            fig.update_layout(
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1,
                    itemclick=False,  # Disable click events
                    itemdoubleclick=False  # Disable double-click events
                )
            )

            # Change the browser tab name using the sensor_name
            fig.update_layout(title_text=f"{sensor_name.upper()}", title_x=0.5)
            
            # Add the figure to the list of figures
            figures.append(fig)

        figures.append(self.__plot_pixels_over_time(sensor_name, ss_data_frame, res*res, masked_df))

        return figures

    def __plot_pixels_over_time(self, sensor_name, ss_data_frame, resolution, t1_dist_df):
        times_col = ss_data_frame.iloc[:, 0]
        times_df = pd.DataFrame({"Time": times_col})
        ss_t1_dist_df = pd.concat([times_df, t1_dist_df.fillna(0)], axis=1)

        fig = make_subplots(rows=1, cols=1)
        fig.update_layout(title=f"{sensor_name.upper()} - Distance over time per Zone", title_x=0.5, xaxis_title='Time (s)', yaxis_title='Distance [mm]', showlegend=True)

        sqrt_res = int(math.sqrt(resolution))
        for i in range(sqrt_res):
            for j in range(sqrt_res-1, -1, -1):
                index = i * sqrt_res + j
                line_name = f'Z{index}'
                visible = True if index == 0 else 'legendonly'
                fig.add_trace(go.Scatter(x=ss_t1_dist_df["Time"], y=ss_t1_dist_df.iloc[:, index+1], mode='lines', name=line_name, visible=visible, legendgroup=f"group{index%(math.sqrt(resolution))}"))

        fig.update_layout(
            legend=dict(
                orientation="h",
                groupclick="toggleitem",
                itemsizing="constant",
                xanchor="center",
                x=0.5
            )
        )

        return fig
    
    def __plot_light_sensor(self, sensor_name, dask_chunk, cols, label):
        layout = go.Layout(
            title=f"{sensor_name.upper()} - Ambient Light Sensor",
            xaxis_title="Time (s)",
            yaxis_title="ADC Count",
            autosize=True
        )
        fig = go.Figure(layout=layout)
        als_lines_colors = ["#000000", "#FF0000", "#999999", "#0000FF", "#00FF00", "#FF00FF"]
        for i, c in enumerate(cols):
            if c != "Time":
                y_column = c
                color = als_lines_colors[i % len(als_lines_colors)]
                fig.add_trace(go.Scatter(x=dask_chunk["Time"], y=dask_chunk[y_column], mode='lines', name=y_column, line=dict(color=color)))
        
        # Add vertical rectangles for labeled data
        if label is not None:
            label_time_tags = self.get_time_tags(label)
            PlotUtils.draw_tags_regions(fig, label_time_tags)
        return fig

    def __plot_presence_sensor(self, sensor_name, dask_chunk, cols, label, software_compensation, embedded_compensation):
        
        # Create a list to store figures to be returned
        figures = []
        
        # create a new layout for the Ambient & Object (raw) figure
        layout = go.Layout(
            title=f"{sensor_name.upper()} - Ambient & Object (raw)",
            xaxis_title="Time (s)",
            yaxis_title="Value",
            autosize=True
        )
        # Create a new figure with the previously defined layout
        fig = go.Figure(layout=layout)
        
        # Plot Ambient and Object (raw) traces
        for idx, c in enumerate(cols[1:3]): # Skip the first column (Time), plot the next two columns (Tambient (raw) and Tobject (raw))
            fig.add_trace(go.Scatter(x=dask_chunk["Time"], y=dask_chunk[c], mode='lines', name=c, line=dict(color=PlotUtils.lines_colors[idx])))
        
        # Add vertical rectangles for labeled data
        if label is not None:
            label_time_tags = self.get_time_tags(label)
            PlotUtils.draw_tags_regions(fig, label_time_tags)
        
        # Add Plot Ambient and Object (raw) figure to the list of figures to be returned
        figures.append(fig)

        # create a new layout for the Presence figure
        layout = go.Layout(
            title=f"{sensor_name.upper()} - Presence",
            xaxis_title="Time (s)",
            yaxis_title="Value",
            autosize=True
        )
        # Create a new figure with the previously defined layout
        fig = go.Figure(layout=layout)

        line_color = "#335c67"
        line_color_sw = "#bb3e03"
        line_color_emb = "#ff9900"
        if software_compensation or embedded_compensation:
            fig = make_subplots(rows=2, cols=1)
            fig.add_trace(go.Scatter(x=dask_chunk["Time"], y=dask_chunk[cols[4]], line=dict(color=line_color), mode='lines', name=cols[4]), row=1, col=1)
            fig.update_xaxes(title_text="Time (s)", row=1, col=1)
            fig.update_yaxes(title_text="Value", row=1, col=1)
            PlotUtils.draw_regions(fig, dask_chunk, "Presence flag", "#97D3C2", 0.5, 1, 1, False)
            if embedded_compensation:
                fig.add_trace(go.Scatter(x=dask_chunk["Time"],  y=dask_chunk[cols[3]], line=dict(color=line_color_emb), mode='lines', name=cols[3]), row=2, col=1)
                fig.update_xaxes(title_text="Time (s)", row=2, col=1)
                fig.update_yaxes(title_text="Value", row=2, col=1)
            if software_compensation:
                fig.add_trace(go.Scatter(x=dask_chunk["Time"], y=dask_chunk[cols[8]], line=dict(color=line_color_sw), mode='lines', name=cols[8]), row=2, col=1)
                PlotUtils.draw_regions(fig, dask_chunk, "Presence flag (sw_comp)", "#FDD891", 0.5, 2, 1, False)
                fig.update_xaxes(title_text="Time (s)", row=2, col=1)
                fig.update_yaxes(title_text="Value", row=2, col=1)
            fig.update_layout(title_text=f"{sensor_name.upper()} - Presence")
        else:
            fig.add_trace(go.Scatter(x=dask_chunk["Time"], y=dask_chunk[cols[34]], line=dict(color=line_color), mode='lines', name=cols[4]))
            PlotUtils.draw_regions(fig, dask_chunk, "Presence flag", "#97D3C2", 0.5, show_label=False)

        # Add vertical rectangles for labeled data
        if label is not None:
            label_time_tags = self.get_time_tags(label)
            PlotUtils.draw_tags_regions(fig, label_time_tags)

        figures.append(fig)

        layout = go.Layout(
            title=f"{sensor_name.upper()} - Motion",
            xaxis_title="Time (s)",
            yaxis_title="Value",
            autosize=True
        )
        fig = go.Figure(layout=layout)
        fig = make_subplots(rows=2, cols=1)

        fig.add_trace(go.Scatter(x=dask_chunk["Time"], y=dask_chunk[cols[6]], line=dict(color=line_color), mode='lines', name=cols[6]), row=1, col=1)
        fig.update_xaxes(title_text="Time (s)", row=1, col=1)
        fig.update_yaxes(title_text="Value", row=1, col=1)
        PlotUtils.draw_regions(fig, dask_chunk, "Motion flag", "#97D3C2", 0.5, 1, 1, False)
        
        fig.add_trace(go.Scatter(x=dask_chunk["Time"], y=dask_chunk[cols[9]], line=dict(color=line_color_sw), mode='lines', name=cols[9]), row=2, col=1)
        PlotUtils.draw_regions(fig, dask_chunk, "Motion flag (sw_comp)", "#FDD891", 0.5, 2, 1, False)
        fig.update_xaxes(title_text="Time (s)", row=2, col=1)
        fig.update_yaxes(title_text="Value", row=2, col=1)
        
        fig.update_layout(title_text=f"{sensor_name.upper()} - Motion")
        figures.append(fig)

        return figures

    def __plot_mems_audio_sensor(self, sensor_name, dask_chunk, cols, dim, subplots, label, raw_flag, unit, fft_params):
        # Create a list to store figures to be returned
        figures = []
        
        # create a new layout for the Ambient & Object (raw) figure
        layout = go.Layout(
            title=sensor_name.upper() if (not raw_flag) else sensor_name.upper() + " (raw)",
            xaxis_title = "Time (s)",
            yaxis_title = UnitMap().unit_dict.get(unit, unit) if (not raw_flag and unit is not None) else "",
            autosize=True
        )
        # Create a new figure with the previously defined layout
        fig = go.Figure(layout=layout)

        if subplots and dim > 1:
            fig = make_subplots(rows=dim, cols=1)
            for i in range(dim):
                fig.add_trace(go.Scatter(x=dask_chunk["Time"], y=dask_chunk[cols[i+1]], line=dict(color=PlotUtils.lines_colors[i]), mode='lines', name=cols[i+1]), row=i+1, col=1)
                fig.update_xaxes(title_text="Time (s)", row=i+1, col=1)
                if not raw_flag and unit is not None:
                    fig.update_yaxes(title_text=UnitMap().unit_dict.get(unit, unit), row=i+1, col=1)
        else:
            if "_ispu" in sensor_name:
                n_lines = len(self.get_ispu_output_column_names())
            else:
                n_lines = dim
            
            for i in range(n_lines):
                fig.add_trace(go.Scatter(x=dask_chunk["Time"], y=dask_chunk[cols[i+1]], line=dict(color=PlotUtils.lines_colors[i]), mode='lines', name=cols[i+1]))
        
        fig.update_layout(title_text=sensor_name.upper() if (not raw_flag) else sensor_name.upper() + " (raw)")
        
        # Add vertical rectangles for labeled data
        if label is not None:
            label_time_tags = self.get_time_tags(label)
            PlotUtils.draw_tags_regions(fig, label_time_tags)

        figures.append(fig)

        if fft_params is not None and ("_acc" in sensor_name or "_mic" in sensor_name):
            
            odr = fft_params[1]
            window_size = 256
            overlap = 128
            step = window_size

            def compute_spectrogram(signal, fs, window_size, step):
                n_windows = (len(signal) - window_size) // step + 1
                spec = []
                for i in range(n_windows):
                    start = i * step
                    end = start + window_size
                    windowed = signal[start:end] * np.hanning(window_size)
                    fft_vals = np.fft.rfft(windowed)
                    power = np.abs(fft_vals) ** 2
                    spec.append(power)
                spec = np.array(spec).T # shape: [frequencies, time]
                times = np.arange(n_windows) * step / fs
                freqs = np.fft.rfftfreq(window_size, d=1/fs)
                return freqs, times, 10 * np.log10(spec + 1e-12) # Convert to dB
            
            if label is not None:
                sw_tag_classes_labels = [value['label'] for value in self.get_sw_tag_classes().values()]
                hw_tag_classes_labels = [value['label'] for value in self.get_hw_tag_classes().values()]
                tag_classes_labels = sw_tag_classes_labels + hw_tag_classes_labels
                fileterd_cols = [c for c in cols.to_list() if c not in tag_classes_labels]
                cols = fileterd_cols
            cc = ['X', 'Y', 'Z'] if (len(cols)) == 3 else ['X', 'Y'] if (len(cols)) == 2 else []
            
            if "_mic" in sensor_name:
                freqs, times, spec = compute_spectrogram(dask_chunk[cols[1]].values, odr, window_size, step)
                mic_fig = go.Figure(data=go.Heatmap(z=spec, x=times, y=freqs, colorscale='Viridis'))
                mic_fig.update_layout(title=f"{sensor_name.upper()} - Spectrogram",
                                    xaxis_title="Time (s)",
                                    yaxis_title="Frequency (Hz)"
                )
                figures.append(mic_fig)
            elif "_acc" in sensor_name:
                axes = cols[1:]
                specs = []
                for axis in axes:
                    freqs, times, spec = compute_spectrogram(dask_chunk[axis].values, odr, window_size, step)
                    specs.append((axis, spec))
                
                acc_mag = np.sqrt(np.sum([dask_chunk[axis].values**2 for axis in axes], axis=0))
                freqs, times, spec = compute_spectrogram(acc_mag, odr, window_size, step)
                specs.append(("ACC Magnitude", spec))

                acc_fig = make_subplots(rows=len(specs), cols=1, shared_xaxes=True, subplot_titles=[f"{axis} - Spectrogram" for axis, _ in specs])
                
                for i, (axis, z) in enumerate(specs, start=1):
                    acc_fig.add_trace(
                        go.Heatmap(
                            z=z, x=times, y=freqs, colorscale='Viridis',
                            showscale=(i == len(specs)),  # Only last subplot gets colorbar
                            colorbar=dict(title="dB") if i == len(specs) else None
                        ),
                        row=i, col=1)
                    acc_fig.update_yaxes(title_text="Frequency (Hz)", row=i, col=1)
            
                acc_fig.update_layout(height=300*len(specs), title_text=f"{sensor_name.upper()} - Spectrogram", xaxis_title="Time (s)")
                figures.append(acc_fig)

        return figures

    # Plots Helper Functions ################################################################################################################

    # Plots Functions #######################################################################################################################
    
    def get_plot_threads(self):
        """
        Returns the list of threads used for plotting.

        Returns:
            list: A list of threads used for plotting.
        """
        return self.plot_threads

    def close_plot_threads(self):
        """
        Closes all plot threads by joining them.
        """
        for t in self.plot_threads:
            t.shutdown()

    def __show_plot_in_browser(self, fig, comp_name, save_plot=False):
        """
        Display the given Plotly figure in a browser using the resampler.show_dash function with a dedicated free port.

        Args:
            fig (plotly.graph_objects.Figure): The Plotly figure to display.
        """
        import socket
        import webbrowser
        from plotly_resampler import FigureResampler
        from dash import Dash, dcc, html

        # Function to find a free port
        def find_free_port():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', 0))
                return s.getsockname()[1]

        # Find a free port
        port = find_free_port()

        # Create a resampler for the figure
        resampler = FigureResampler(fig, default_n_shown_samples=1000)

        # Save plots as HTML files if required
        if save_plot:
            html_file = os.path.join(self.get_acquisition_path(), f"{comp_name}.html")
            resampler.write_html(html_file)

        app = Dash(__name__)
        app.title = f"{comp_name}"
        
        has_heatmap = any(isinstance(trace, go.Heatmap) for trace in fig.data)
        if "_tof" in comp_name and has_heatmap:
            app.layout = html.Div([
                dcc.Graph(figure=fig, style={"height": "95vh"}),
            ])
        else:
            app.layout = html.Div([
                dcc.Graph(id="my-graph", figure=resampler, style={"height": "95vh"}),
            ])
            # Register the resampler callback
            resampler.register_update_graph_callback(app, "my-graph")

        pt = ServerThread(app, port)
        pt.start()
        self.plot_threads.append(pt)
        
        webbrowser.open(f"http://127.0.0.1:{port}")

    def get_dask_df(self, comp_name, comp_status, start_time=0, end_time=-1, label=None, raw_flag=False):
        from stdatalog_core.HSD.HSDatalog import HSDatalog
        try:
            labeled = label is not None
            # Define the file path for the parquet file
            file_path = os.path.join(self.get_acquisition_path(), f'{comp_name}.parquet')
            # Convert data to parquet format if the file does not exist
            if not os.path.exists(file_path):
                HSDatalog.convert_dat_to_xsv(self, {comp_name:comp_status}, start_time, end_time, labeled, raw_flag, self.get_acquisition_path(), "PARQUET")
            
            # Read the parquet file into a Dask dataframe
            dask_df = dd.read_parquet(file_path)
            return dask_df
        except Exception as err:
            log.exception(err)
            return None

    def get_sensor_plot(self, sensor_name, sensor_status, start_time = 0, end_time = -1, label=None, which_tags = [], subplots=False, raw_flag = False, fft_plots = False, save_plots = False):
        from stdatalog_core.HSD.HSDatalog import HSDatalog
        try:
            labeled = label is not None
            # Define the file path for the parquet file
            file_path = os.path.join(self.get_acquisition_path(), f'{sensor_name}.parquet')
            # Convert data to parquet format (overwriting it if already exists)
            HSDatalog.convert_dat_to_xsv(self, {sensor_name:sensor_status}, start_time, end_time, labeled, raw_flag, self.get_acquisition_path(), "PARQUET")
            # Read the parquet file into a Dask dataframe
            dask_df = None
            if os.path.exists(file_path):
                dask_df = dd.read_parquet(file_path)
            else:
                return

            # Uncomment the following lines to log the number of rows and min/max values for each row group
            # parquet_file = pq.ParquetFile(file_path)
            # # Log the number of rows and min/max values for each row group
            # for i in range(parquet_file.metadata.num_row_groups):
            #     user_id_col_stats = parquet_file.metadata.row_group(i).column(0).statistics
            #     print("---------------------------------------------------------------------------------------------")
            #     print(f"Processing sensor: {sensor_name}")
            #     print(f"Number of row groups: {parquet_file.metadata.num_row_groups}")
            #     print(f"row group: {i}, num of rows: {user_id_col_stats.num_values}, min: {user_id_col_stats.min}, max: {user_id_col_stats.max}")
            #     print("---------------------------------------------------------------------------------------------")

            # Get the acquisition label classes
            acq_label_classes = self.get_acquisition_label_classes()
            # Filter columns to plot
            columns_to_plot = [item for item in dask_df.columns if item not in acq_label_classes]

            if save_plots:
                # Define the output directory for saving plots
                output_dir = "plots"
                os.makedirs(output_dir, exist_ok=True)
            
            sensor_category = sensor_status.get("sensor_category")
            figures = []

            # Iterate over each chunk of the Dask dataframe
            for chunk in dask_df.to_delayed():
                chunk = chunk.compute()
                
                # Uncomment the following lines to log the size of each chunk
                # with open("dask_log.txt", "a") as log_file:
                #     log_file.write(f"Processing chunk for sensor: {sensor_name}, size: {len(chunk)} rows, partitions: {dask_df.partitions}\n")
                
                if sensor_category == SensorCategoryEnum.ISENSOR_CLASS_RANGING.value:
                    resolution = sensor_status.get("resolution")
                    if resolution is not None:
                        res = int(resolution.split("x")[0])
                        output_format = sensor_status.get("output_format")
                        figures = self.__plot_ranging_sensor(sensor_name, chunk, res, output_format)
                elif sensor_category == SensorCategoryEnum.ISENSOR_CLASS_LIGHT.value:
                    fig = self.__plot_light_sensor(sensor_name, chunk, columns_to_plot, label)
                    figures.append(fig)
                    pass
                elif sensor_category == SensorCategoryEnum.ISENSOR_CLASS_PRESENCE.value:
                    figures = self.__plot_presence_sensor(sensor_name, chunk, columns_to_plot, label, sensor_status.get("software_compensation"), sensor_status.get("embedded_compensation"))
                    pass
                else: # ISENSOR_CLASS_MEMS and ISENSOR_CLASS_AUDIO
                    fft_params = None
                    if fft_plots:
                        odr = sensor_status.get('odr', 1)
                        fft_params = (fft_plots, odr)
                    figures = self.__plot_mems_audio_sensor(sensor_name, chunk, columns_to_plot, len(columns_to_plot)-1, subplots, label, raw_flag, sensor_status.get('unit'), fft_params)
                
                for fig in figures:
                    self.__show_plot_in_browser(fig, sensor_name, save_plot=save_plots)
            
            # Delete the Parquet file after processing
            if os.path.exists(file_path):
                os.remove(file_path)

        except MissingISPUOutputDescriptorException as ispu_err:
            # Handle missing ISPU output descriptor exception
            log.error(ispu_err)
            log.warning("Copy the right ISPU output descriptor file in your \"{}\" acquisition folder renamed as \"ispu_output_format.json\"".format(self.get_acquisition_path()))
        except Exception as err:
            log.exception(err)
    
    def get_actuator_plot(self, actuator_name, actuator_status, start_time = 0, end_time = -1, label=None, which_tags = [], subplots=True, raw_flag = False, save_plots = False):
        self.get_sensor_plot(actuator_name, actuator_status, start_time, end_time, label, which_tags, True, raw_flag, save_plots=save_plots)

    def get_algorithm_plot(self, algorithm_name, algorithm_status, start_time = 0, end_time = -1, label=None, which_tags = [], subplots=False, raw_flag = False):
        from stdatalog_core.HSD.HSDatalog import HSDatalog
        try:
            labeled = label is not None
            # Define the file path for the parquet file
            file_path = os.path.join(self.get_acquisition_path(), f'{algorithm_name}.parquet')
            # Convert data to parquet format (overwriting it if already exists)
            HSDatalog.convert_dat_to_xsv(self, {algorithm_name:algorithm_status}, start_time, end_time, labeled, raw_flag, self.get_acquisition_path(), "PARQUET")
            # Read the parquet file into a Dask dataframe
            dask_df = None
            if os.path.exists(file_path):
                dask_df = dd.read_parquet(file_path)
            else:
                return

            if dask_df is not None:
                algo_type = algorithm_status.get("algorithm_type")
                if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                    s_dim = algorithm_status.get("fft_length")
                else:
                    s_dim = algorithm_status.get("dim")
                
                # Get the acquisition label classes
                acq_label_classes = self.get_acquisition_label_classes()
                # Filter columns to plot
                columns_to_plot = [item for item in dask_df.columns if item not in acq_label_classes]
                
                if algo_type == AlgorithmTypeEnum.IALGORITHM_TYPE_FFT.value:
                    fft_length = s_dim
                    
                    fft_sample_freq = algorithm_status.get("fft_sample_freq")
                    if fft_sample_freq is None:
                        log.error("FFT Sample Freq. unknown")
                        raise MissingPropertyError("fft_sample_freq")
                    
                    for chunk in dask_df.to_delayed():
                        chunk = chunk.compute()

                        # Prepare data for spectrogram
                        df_array = chunk.iloc[:, 1:].T.to_numpy(dtype="float")
                        y_value = np.square(df_array)
                        y_value = y_value / (fft_length * fft_sample_freq)
                        y_value = 10 * np.log10(y_value)
                        
                        freqs = np.linspace(0, fft_sample_freq / 2, y_value.shape[0])
                        # Plotly spectrogram
                        fig = go.Figure(
                            data=go.Heatmap(
                                z=y_value,
                                y=freqs,
                                colorscale="Viridis",
                                colorbar=dict(
                                    title="dB",
                                    len=1.0,      # Full subplot height
                                    y=0.5,        # Centered vertically
                                    yanchor='middle'
                                )
                            )
                        )
                        fig.update_layout(
                            title=f"{algorithm_name.upper()} - Spectrogram",
                            # xaxis_title="Time (s)", #TODO
                            yaxis_title="Frequency (Hz)",
                            height=600
                        )

                        self.__show_plot_in_browser(fig, algorithm_name)
                else:
                    log.error("Algorithm type selected is not supported.")
            
            else:
                log.error("Empty DataFrame extracted.")
            
            # Delete the Parquet file after processing
            if os.path.exists(file_path):
                os.remove(file_path)

        except MissingPropertyError as exc:
            log.error("Missing {} Property Error!".format(exc))
            raise
        except MemoryError:
            log.error("Memory Error occoured! You should batch process your {} file".format(FileManager.encode_file_name(algorithm_name)))
            raise
        except  ValueError:
            log.error("Value Error occoured! You should batch process your {} file".format(FileManager.encode_file_name(algorithm_name)))
            raise
    # Plots Functions #######################################################################################################################

    # #======================================================================================#
    ### OFFLINE CLI Interaction ##############################################################
    #========================================================================================#
    def prompt_device_id_select_CLI(self, device_list):
        selected_device = CLI.select_item("Device",device_list)
        selected_device_id = device_list.index(selected_device)
        return selected_device_id

    def prompt_sensor_select_CLI(self, sensor_list = None):
        if sensor_list is None:
            sensor_list = self.get_sensor_list()
        return CLI.select_item("PnPL_Component", sensor_list)
    
    def prompt_algorithm_select_CLI(self, algo_list = None):
        if algo_list is None:
            algo_list = self.get_algorithm_list()
        return CLI.select_item("PnPL_Component", algo_list)
    
    def prompt_actuator_select_CLI(self, actuator_list = None):
        if actuator_list is None:
            actuator_list = self.get_actuator_list()
        return CLI.select_item("PnPL_Component", actuator_list)
    
    def prompt_actuator_telemetries_select_CLI(self, actuator_stat, telemetry_keys):
        telemetry_list = []
        for tk in telemetry_keys:
            telemetry_list.append({tk:actuator_stat[tk]})
        return CLI.select_items("PnPL_Component", telemetry_list)
    
    def prompt_component_select_CLI(self, component_list = None):
        if component_list is None:
            sensor_list = self.get_sensor_list()
            algo_list = self.get_algorithm_list()
            component_list = sensor_list + algo_list
        return CLI.select_item("PnPL_Component", component_list)

    def prompt_file_select_CLI(self, dat_file_list = None):
        if dat_file_list is None:
            dat_file_list = FileManager.get_file_names_from_model()
        return CLI.select_item("Data File", dat_file_list)

    def prompt_label_select_CLI(self, label_list = None):
        if label_list is None or len(label_list) == 0:
            label_list = self.get_acquisition_label_classes()
        return CLI.select_item("Labels", label_list)

    def present_device_info(self, device_info = None):
        """
        Presents the device information to the user interface, typically through the command line interface (CLI).
        If no device information is provided, it retrieves the default device information using the get_device_info method.
        :param device_info: The device information to be presented. If None, the method retrieves the device information using the get_device_info method.
        """
        # Check if device information has been provided
        if device_info is None:
            # If not, retrieve the default device information using the 'get_device_info' method
            device_info = self.get_device_info()
        # Use the CLI (Command Line Interface) module to present the device information.
        # This could involve printing the information to the console, displaying it in a GUI, etc.
        CLI.present_item(device_info)

    def present_sensor_list(self, sensor_list = None):
        """
        Presents a list of sensors to the user interface, typically through the command line interface (CLI).
        If no sensor list is provided, it retrieves the default sensor list using the get_sensor_list method.
        :param sensor_list: [Optional] A list of sensors to be presented. If None, the method retrieves the sensor list using the get_sensor_list method.
        """
        # Check if a sensor list has been provided
        if sensor_list is None:
            # If not, retrieve the default sensor list using the 'get_sensor_list' method
            sensor_list = self.get_sensor_list()
        
        # Use the CLI (Command Line Interface) module to present the items in the sensor list.
        # This could involve printing the list to the console, displaying it in a GUI, etc.
        CLI.present_items(sensor_list)

    def present_sw_tag_classes(self, tag_class_list = None):
        """
        Presents a list of software tag classes to the user interface, typically through the command line interface (CLI).
        If no list is provided, it retrieves the software tag classes using the get_sw_tag_classes method.

        :param tag_class_list: [Optional] A list of software tag classes to be presented. If None, the method retrieves the list using the get_sw_tag_classes method.
        """
        # Check if a list of software tag classes has been provided
        if tag_class_list is None:
            # If not, retrieve the list using the 'get_sw_tag_classes' method
            tag_class_list = self.get_sw_tag_classes()
        
        # Use the CLI (Command Line Interface) module to present the list of software tag classes.
        # This could involve printing the list to the console, displaying it in a GUI, etc.
        CLI.present_items(tag_class_list)

    def present_hw_tag_classes(self, tag_class_list = None):
        """
        Presents a list of hardware tag classes to the user interface, typically through the command line interface (CLI).
        If no list is provided, it retrieves the hardware tag classes using the get_hw_tag_classes method.

        :param tag_class_list: [Optional] A list of hardware tag classes to be presented. If None, the method retrieves the list using the get_hw_tag_classes method.
        """
        # Check if a list of hardware tag classes has been provided
        if tag_class_list is None:
            # If not, retrieve the list using the 'get_hw_tag_classes' method
            tag_class_list = self.get_hw_tag_classes()
        # Use the CLI (Command Line Interface) module to present the list of hardware tag classes.
        # This could involve printing the list to the console, displaying it in a GUI, etc.
        CLI.present_items(tag_class_list)

    def present_sensor(self, sensor):
        """
        Presents information about a specific sensor to the user interface, typically through the command line interface (CLI).
        If the sensor information is provided, it is presented using the CLI module. If not, a warning is issued.

        :param sensor: The sensor information to be presented. If None, a warning message is displayed.
        """
        # Check if sensor information has been provided
        if sensor is not None:
            # If sensor information is available, use the CLI (Command Line Interface) module to present it.
            # This could involve printing the information to the console, displaying it in a GUI, etc.
            CLI.present_item(sensor)
        else:
            # If no sensor information is provided, display a warning message to the user.
            log.warning("No sensor selected")
