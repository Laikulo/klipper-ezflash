Hirerarchy
* Pirmary Category (e.g. mainboards)
  * manufactuerr
    * Product name
      * Product variant
        * (descriptior)

Fields for descriptiors:
* mcu
  * mcu (as per Klipper's MCU field)
  * clock (in Mhz)
* CAN_Bridge (empty object/absent/null means not supported)
  * can_tx (pin_name)
  * can_rx (pin_name)
* usb
  * "${PIN}/${PIN}"
* uart
* status (pin_name)
* klipper_options
  * serial_number - str
  * ... (per target)
