# Xiaomi Vacuum Map Camera


**WARNING!**

This version of integration is not fully tested.

### Requirements

Home Assistant must have ssh access to the vacuum (it must be able to connect via ssh without password authentication)

### Example configuration

```yaml
camera:
  - platform: xiaomi_vacuum_map
    vacuum_ssh: "root@192.168.0.123"
    vacuum_entity: "vacuum.roborock"
```

### Known issues
* Errors in logs after start of HA
* Map not being available until the first run of the vacuum.