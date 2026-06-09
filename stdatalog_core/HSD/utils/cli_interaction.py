"""CLI helpers for presenting HSD model objects and interactively selecting items.

This module:
- Prints concise and detailed views for Devices, Sensors, Tags, and generic PnPL components.
- Provides interactive selection utilities for single and multiple items.
"""

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
# Module purpose:
#   - Provide simple CLI helpers to present model objects and interactively select items.
#   - Support selection and presentation of Devices, Sensors, Tags and generic PnPL components.
#
# Design notes:
#   - This module prints to stdout and reads from stdin; intended for interactive console use.
#   - It relies on the HSD data model classes for type-specific formatting.
#   - Selection helpers are resilient to invalid inputs and allow quitting gracefully.
#
# Error handling:
#   - Invalid user inputs are re-prompted; 'q' quits selection.
#   - When lists are empty, functions log an error and return None.

from stdatalog_core.HSD.model.DeviceConfig import Device, DeviceInfo, Sensor, HwTag, SwTag
from stdatalog_core.HSD.model.AcquisitionInfo import Tag
from stdatalog_core.HSD_utils import logger

log = logger.get_logger(__name__)


class CLIInteraction:
    """
    CLI utilities for selecting and presenting HSD model items.

    Methods:
        - select_item(what, a_list): interactively choose a single item from a list.
        - select_items(what, a_list): interactively choose multiple items by indices.
        - present_items(item_list): print a concise list of items with status.
        - present_item(item): print detailed information for a single item.

    Usage:
        selected = CLIInteraction.select_item("Sensor", sensors)
        CLIInteraction.present_item(selected)
    """

    @staticmethod
    def _item_label(what: str, item) -> str:
        """
        Generate a concise label for an item based on its type.
        Parameters:
            what: str
                Label describing the item type (e.g., "Device", "Sensor", "Tag", "PnPL_Component").
            item: Device | Sensor | Tag | dict | any
                The item to label.
        Returns:
            str:
                A concise string label for the item.
        """
        if isinstance(item, Device):
            return f"{item.device_info.alias} [{item.device_info.part_number}]"
        if isinstance(item, Sensor):
            return item.name
        if isinstance(item, Tag):
            return item.label
        if what == "PnPL_Component" and isinstance(item, dict):
            # dict-like component: use the component key
            return next(iter(item))
        return str(item)

    @staticmethod
    def select_item(what: str, a_list: list):
        """
        Interactively select a single item by index from a list.
        Parameters:
            what: str
                Label describing the items (e.g., "Sensor", "Device", "Tag", "PnPL_Component").
            a_list: list
                List of items to choose from.
        Returns:
            item:
                The selected item from the list, or None if the user quits.
        Behavior:
            - Prints indices and labels for each item.
            - Prompts user to input an index or 'q' to quit.
            - Validates input and re-prompts on invalid entries.
        """
        if not a_list:
            log.error(f"==> No {what} in list")
            return None

        while True:
            # Render menu entries
            for i, c in enumerate(a_list):
                print(f"{i} - {CLIInteraction._item_label(what, c)}")
            print("q - Quit")

            choice = input(f"Select one {what} ('q' to quit) ==> ")
            if choice == "q":
                print("Bye!")
                return None

            try:
                idx = int(choice)
            except ValueError:
                print("please input a correct value")
                continue

            if 0 <= idx < len(a_list):
                return a_list[idx]

            print("please input a correct value")

    @staticmethod
    def select_items(what: str, a_list: list):
        """
        Interactively select multiple items by index from a list.

        Parameters:
            what: str
                Label describing the items (e.g., "PnPL_Component").
            a_list: list
                List of dict-like items if what == "PnPL_Component", else generic items.

        Returns:
            list[str]:
                Selected element identifiers. For PnPL components,
                this is the dict key (component name).

        Behavior:
            - Prints indices and labels.
            - Accepts space-separated indices per prompt iteration.
            - Special inputs:
                'all'  -> selects all elements (dict keys)
                'done' -> finishes selection
            - Validates that indices are within range; informs user on invalid entries.

        Notes:
            - For non-PnPL lists, selected_elements will still contain dict keys if items are dicts.
        """
        selected_elements = []
        print("Enter the indices of the elements you want to select (space-separated).")
        print("Type 'done' when you're finished. 'all' to select all elements")
        done_flag = False

        while not done_flag:
            # Display the selectable list each loop to help the user
            for i, c in enumerate(a_list):
                if what == "PnPL_Component":
                    item_id = list(c.keys())[0]
                else:
                    item_id = str(c)
                print(str(i) + " - " + item_id)
            index_input = input("Index: ")

            ids = index_input.split(" ")
            for _id in ids:
                if _id == "all":
                    for e in a_list:
                        # For dict-like components, use the key as identifier
                        selected_elements.append(list(e.keys())[0])
                    done_flag = True
                if _id == "done":
                    done_flag = True
                else:
                    try:
                        _id = int(_id)
                        if 0 <= _id < len(a_list):
                            selected_elements.append(list(a_list[_id].keys())[0])
                        else:
                            print("Invalid index! Try again.")
                    except ValueError:
                        print("Invalid input! Please enter a valid index or 'done'.")
        print("Selected elements:", selected_elements)
        return selected_elements

    @staticmethod
    def _print_sensor_summary(sensor: Sensor):
        """
        Print a concise summary of a Sensor and its sub-sensors.
        Parameters:
            sensor: Sensor
        Prints:
            Sensor name and ID, followed by each sub-sensor's type and status.
        """
        print(f"{sensor.id}) {sensor.name}")
        sd_list = sensor.sensor_descriptor.sub_sensor_descriptor
        ss_list = sensor.sensor_status.sub_sensor_status
        for i, ssd in enumerate(sd_list):
            inactive = not ss_list[i].is_active
            suffix = " --> [INACTIVE]" if inactive else ""
            print(f"    {ssd.id}) {ssd.sensor_type}{suffix}")

    @staticmethod
    def _print_tag(tag):
        """
        Print a concise summary of a Tag (HwTag or SwTag).
        Parameters:
            tag: HwTag | SwTag
        Prints:
            Tag label and status (enabled/disabled for HwTag).
        Returns:
            bool:
                True if tag was printed, False otherwise.
        """
        if isinstance(tag, HwTag):
            status = "ENABLED" if tag.enabled else "DISABLED"
            print(f"- {tag.label} [{status}]")
            return True
        if isinstance(tag, SwTag):
            print(f"- {tag.label}")
            return True
        return False

    @staticmethod
    def _print_pnpl_from_str(item: str, container):
        """
        Print a concise summary of a PnPL component given its string identifier and container.
        Parameters:
            item: str
                The string identifier of the PnPL component.
            container: dict
                The container holding the PnPL components.
        Prints:
            Component name and status or label if applicable.
        """
        comp = container[item] if isinstance(container, dict) and item in container else None
        if not isinstance(comp, dict):
            print(f"- {item}")
            return
        c_type = comp.get("c_type")
        if c_type == 0:
            enabled = comp.get("enable", True)
            print(f"- {item}" + ("" if enabled else " --> [INACTIVE]"))
            return
        label = comp.get("label")
        if label is not None and ("sw_tag" in item or "hw_tag" in item):
            print(f"- {item} label: {label}")
            return
        print(f"- {item}")

    @staticmethod
    def _print_pnpl_from_dict(item: dict):
        """
        Print a concise summary of a PnPL component given as a dict.
        Parameters:
            item: dict
                The dict representing the PnPL component.
        Prints:
            Component name and status or label if applicable.
        """
        key = next(iter(item))
        comp = item[key]
        c_type = comp.get("c_type")
        if c_type == 0:
            enabled = comp.get("enable", True)
            print(f"- {key}" + ("" if enabled else " --> [INACTIVE]"))
            return
        label = comp.get("label")
        if label is not None and ("sw_tag" in key or "hw_tag" in key):
            print(f"- {key} label: {label}")
            return
        print(f"- {key}")

    @staticmethod
    def present_items(item_list: list):
        """
        Print a concise summary of a list of items.
        Parameters:
            item_list: list
                List of items to present. Items may be:
                - Sensor instances
                - Tag instances (HwTag, SwTag)
                - str identifiers for PnPL components
                - dict-like PnPL components
                - generic objects
        Prints:
            A summary line for each item in the list.
        Returns:
            None. Prints to stdout.
        """
        for item in item_list:
            if isinstance(item, Sensor):
                CLIInteraction._print_sensor_summary(item)
                continue

            if CLIInteraction._print_tag(item):
                continue

            if isinstance(item, str):
                # item_list might be dict keyed by component names, or a list
                container = item_list if isinstance(item_list, dict) else {}
                CLIInteraction._print_pnpl_from_str(item, container)
                continue

            if isinstance(item, dict):
                CLIInteraction._print_pnpl_from_dict(item)
                continue

            print(f"- {item}")

    @staticmethod
    def present_item(item):
        """
        Print a detailed view of a single item.

        Parameters:
            item: DeviceInfo | Sensor | dict
                - DeviceInfo: prints device metadata and optional BLE MAC address
                - Sensor: prints descriptor and status of each sub-sensor
                - dict (PnPL component): prints component name and key/value pairs

        Returns:
            None. Prints to stdout.
        """
        if isinstance(item, DeviceInfo):
            print(f"Serial number: {item.serial_number}")
            if item.model is not None:
                print(f"Model: {item.model}")
            print(f"Device alias: {item.alias}")
            print(f"Part number: {item.part_number}")
            print(f"Website: {item.url}")
            print(f"FW name: {item.fw_name}")
            print(f"FW version: {item.fw_version}")
            print(f"Data file extension: {item.data_file_ext}")
            print(f"Data file format: {item.data_file_format}")
            if item.ble_mac_address is not None:
                print(f"BLE MAC address: {item.ble_mac_address}")
        elif isinstance(item, Sensor):
            s_descriptor_list = item.sensor_descriptor.sub_sensor_descriptor
            s_status_list = item.sensor_status.sub_sensor_status
            for i, ssd in enumerate(s_descriptor_list):
                print(f"\n[{item.name}] - ID: {ssd.id}")
                print("- DESCRIPTOR:")
                print(f" -- Sensor Type: {ssd.sensor_type}")
                print(f" -- Dimensions: {ssd.dimensions}")
                print(f" -- Dimensions Labels: {ssd.dimensions_label}")
                print(f" -- Unit: {ssd.unit}")
                print(f" -- Data Type: {ssd.data_type}")
                print(f" -- Supported FS: {ssd.fs}")
                print(f" -- Supported ODR: {ssd.odr}")
                print(" -- Samples per Timestamp info:")
                print(f"   - Min: {ssd.samples_per_ts.min}")
                print(f"   - Max: {ssd.samples_per_ts.max}")
                print(f"   - Data Type: {ssd.samples_per_ts.data_type}")
                print("- STATUS:")
                print(f" -- isActive: {s_status_list[i].is_active}")
                print(f" -- ODR: {s_status_list[i].odr}")
                print(f" -- FS: {s_status_list[i].fs}")
                print(f" -- samples per timestamp: {s_status_list[i].samples_per_ts}")
                print(f" -- sensitivity: {s_status_list[i].samples_per_ts}")
                print(f" -- initial_offset: {s_status_list[i].initial_offset}")
        else:
            # Generic PnPL component: dict keyed by component name
            item_key = list(item.keys())[0]
            print(f"\n[{item_key} PnPL Component]")
            component_status = item[item_key]
            for p in component_status:
                print(f"-- {p}: {component_status[p]}")
