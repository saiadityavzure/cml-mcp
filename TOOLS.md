# CML MCP Server — Tool Reference

All tools use **human-readable names** instead of UUIDs. Labs are identified by title, nodes by label, and links by the two node labels they connect. UUIDs are resolved internally and never need to be supplied by the caller.

---

## Labs

### `get_cml_labs`
List all labs on the CML server. Optionally filter by owner username.
- **Inputs:** `user` (optional username)
- **Returns:** List of lab objects with id, title, state, owner, and node count.

### `create_empty_lab`
Create a new empty lab with no nodes or links.
- **Inputs:** `lab` — object with optional `title`, `description`, `notes`, `owner`, `associations`.
- **Returns:** UUID of the new lab.

### `create_full_lab_topology`
Import a complete lab from a topology definition (YAML/dict).
- **Inputs:** `topology` — full topology object or YAML string.
- **Returns:** UUID of the created lab.

### `modify_cml_lab`
Update metadata of an existing lab.
- **Inputs:** `lab_name`, `lab` — object with fields to update (`title`, `description`, `notes`, `owner`, `associations`).
- **Returns:** `true` on success.
- **Note:** Previously required `lid` UUID. Now resolves by name.

### `start_cml_lab`
Start all nodes in a lab.
- **Inputs:** `lab_name`, `wait_for_convergence` (optional bool).
- **Returns:** `true` on success.
- **Note:** Uses SSE heartbeat to keep the connection alive during long start operations.

### `stop_cml_lab`
Stop all running nodes in a lab.
- **Inputs:** `lab_name`.
- **Returns:** `true` on success.
- **Note:** Uses SSE heartbeat for long stop operations.

### `wipe_cml_lab`
Wipe all node data and configurations in a lab. Destructive — asks for confirmation.
- **Inputs:** `lab_name`.
- **Returns:** `true` on success.

### `delete_cml_lab`
Delete a lab. Auto-stops and wipes before deleting. Destructive — asks for confirmation.
- **Inputs:** `lab_name`.
- **Returns:** `true` on success.

### `get_cml_lab_by_title`
Find a lab by exact title and return its full details.
- **Inputs:** `title`.
- **Returns:** Lab object with id, state, nodes, and metadata.

### `download_lab_topology`
Download the full topology of a lab as a YAML string.
- **Inputs:** `lab_name`.
- **Returns:** YAML string representing the lab topology.
- **Note:** Previously required `lid` UUID.

### `clone_cml_lab`
Clone an existing lab. Downloads topology and re-imports it under a new title.
- **Inputs:** `lab_name`, `new_title` (optional — defaults to `"Copy of <original title>"`).
- **Returns:** UUID of the new cloned lab.
- **Note:** Previously required `lid` UUID.

---

## Nodes

### `get_nodes_for_cml_lab`
Get all nodes in a lab with their state and operational data.
- **Inputs:** `lab_name`.
- **Returns:** List of node objects with id, label, definition, x/y position, state, interfaces, CPU, and RAM.
- **Note:** Previously required `lid` UUID.

### `add_node_to_cml_lab`
Add a new node to a lab. Canvas position is chosen automatically to avoid overlap with existing nodes.
- **Inputs:** `lab_name`, `node_definition` (e.g. `"alpine"`, `"iosv"`, `"iol-xe"`), `label`, and optional `image_definition`, `ram`, `cpus`, `cpu_limit`, `data_volume`, `boot_disk_size`, `tags`, `configuration`.
- **Returns:** UUID of the new node.
- **Note:** Previously required passing x/y coordinates and a nested NodeCreate object. Now fully flat with auto-placement.

### `configure_cml_node`
Set a node's startup configuration.
- **Inputs:** `lab_name`, `node_label`, `config` (plain string of device commands).
- **Returns:** `true` on success.
- **Note:** Node must be stopped or not yet started. Previously required `lid` and `nid` UUIDs.

### `start_cml_node`
Power on a node.
- **Inputs:** `lab_name`, `node_label`, `wait_for_convergence` (optional bool).
- **Returns:** `true` on success.
- **Note:** Uses SSE heartbeat to prevent timeout on slow-booting nodes.

### `stop_cml_node`
Power off a node.
- **Inputs:** `lab_name`, `node_label`.
- **Returns:** `true` on success.

### `wipe_cml_node`
Erase all runtime data from a node. Node must be stopped first.
- **Inputs:** `lab_name`, `node_label`.
- **Returns:** `true` on success.
- **Note:** Resets node to `DEFINED_ON_CORE` state. Previously had an elicit confirmation dialog that caused SSE disconnects — removed.

### `delete_cml_node`
Remove a node from a lab. Auto-stops and wipes before deleting.
- **Inputs:** `lab_name`, `node_label`.
- **Returns:** `true` on success.
- **Note:** Previously had an elicit confirmation dialog — removed.

---

## Interfaces

### `get_interfaces_for_node`
List all interfaces on a node.
- **Inputs:** `lab_name`, `node_label`.
- **Returns:** List of interface objects with id, label, slot, type, MAC address, and connection state.
- **Note:** Previously required `lid` and `nid` UUIDs.

### `add_interface_to_node`
Add a new interface slot to a node.
- **Inputs:** `lab_name`, `node_label`, `slot` (optional), `mac_address` (optional).
- **Returns:** The newly created interface object.
- **Note:** If the node has been booted before, its physical config is locked. The tool automatically stops and wipes the node first, then adds the interface. Reconfigure and restart the node afterward.

---

## Links

### `connect_nodes_by_label`
Create a link between two nodes. Finds the first free interface on each node, creating a new slot if needed.
- **Inputs:** `lab_name`, `node_a_label`, `node_b_label`.
- **Returns:** UUID of the new link.

### `get_all_links_for_lab`
List all links in a lab.
- **Inputs:** `lab_name`.
- **Returns:** List of link objects with id, label, interface_a, interface_b, node_a, node_b, and state.
- **Note:** Previously required `lid` UUID.

### `start_cml_link`
Enable connectivity on a link.
- **Inputs:** `lab_name`, `node_a_label`, `node_b_label`.
- **Returns:** `true` on success.
- **Note:** Link order is direction-independent. Previously required `lid` and `link_id` UUIDs.

### `stop_cml_link`
Disable connectivity on a link (simulates a link failure).
- **Inputs:** `lab_name`, `node_a_label`, `node_b_label`.
- **Returns:** `true` on success.
- **Note:** Previously required `lid` and `link_id` UUIDs.

### `apply_link_conditioning`
Apply network impairment to a link (bandwidth, latency, loss, jitter, etc.).
- **Inputs:** `lab_name`, `node_a_label`, `node_b_label`, `condition` — object with optional fields: `bandwidth` (kbps), `latency` (ms), `loss` (%), `jitter` (ms), `duplicate` (%), `enabled` (bool), and correlation fields.
- **Returns:** `true` on success.
- **Note:** Previously required `lid` and `link_id` UUIDs.

---

## CLI & Console

### `get_console_log`
Retrieve the console output history of a node.
- **Inputs:** `lab_name`, `node_label`.
- **Returns:** List of log entries with `time` (ms since boot) and `message`.
- **Note:** Node must be started. Previously required `lid` and `nid` UUIDs.

### `send_cli_command`
Send exec or config commands to a running node via pyATS.
- **Inputs:** `lab_name`, `node_label`, `commands` (newline-separated), `config_command` (bool, default `false`).
- **Returns:** Command output as text.
- **Note:** Node must be in BOOTED state. `config_command=true` enters config mode automatically — omit `configure terminal` and `end`. Previously required `lid` UUID and a separate `label` parameter.

---

## Annotations

### `get_annotations_for_cml_lab`
List all visual annotations on a lab canvas.
- **Inputs:** `lab_name`.
- **Returns:** List of annotation objects (text, rectangle, ellipse, or line).
- **Note:** Previously required `lid` UUID.

### `add_annotation_to_cml_lab`
Add a visual annotation to a lab canvas.
- **Inputs:** `lab_name`, `annotation` — object with `type` (`"text"` / `"rectangle"` / `"ellipse"` / `"line"`) and type-specific fields.
- **Returns:** UUID of the new annotation.
- **Note:** For rectangles/ellipses, `x2`/`y2` are width/height from `x1`/`y1`, not absolute corners. For lines, `x2`/`y2` are absolute endpoint coordinates. Previously required `lid` UUID.

### `delete_annotation_from_lab`
Delete an annotation from a lab canvas.
- **Inputs:** `lab_name`, `annotation_id` (UUID from get or add call).
- **Returns:** `true` on success.
- **Note:** `annotation_id` remains UUID-based — annotations have no human-readable names. Previously required `lid` UUID.

---

## Packet Capture (PCAP)

All PCAP tools identify the target link by the two node labels it connects, in any order.

### `start_packet_capture`
Begin capturing packets on a link.
- **Inputs:** `lab_name`, `node_a_label`, `node_b_label`, `pcap` — object with at least one of `maxtime` (seconds) or `maxpackets`.
- **Returns:** `true` on success.
- **Note:** Previously required `lid` and `link_id` UUIDs.

### `stop_packet_capture`
Stop an active packet capture on a link.
- **Inputs:** `lab_name`, `node_a_label`, `node_b_label`.
- **Returns:** `true` on success.

### `check_packet_capture_status`
Check whether a capture is active and how many packets have been collected.
- **Inputs:** `lab_name`, `node_a_label`, `node_b_label`.
- **Returns:** Capture config and current packet count.

### `get_captured_packet_overview`
Get a summary of each captured packet.
- **Inputs:** `lab_name`, `node_a_label`, `node_b_label`.
- **Returns:** List of PCAPItem objects with per-packet metadata.

### `get_packet_capture_data`
Download the full capture as a binary PCAP file.
- **Inputs:** `lab_name`, `node_a_label`, `node_b_label`.
- **Returns:** Base64-encoded PCAP file. Decode and save as `.pcap` for use with Wireshark or tcpdump.

---

## Node Definitions

### `get_cml_node_definitions`
List all node types available on the CML server.
- **Inputs:** None.
- **Returns:** List of node definition IDs (e.g. `"alpine"`, `"iosv"`, `"iol-xe"`).

### `get_node_definition_detail`
Get full detail for a specific node type.
- **Inputs:** `did` — node definition ID.
- **Returns:** Full definition including available image definitions, resource defaults, and interface count.

---

## System

### `get_cml_information`
Get general CML server information (version, hostname).
- **Inputs:** None.

### `get_cml_status`
Get the current operational status of the CML server.
- **Inputs:** None.

### `get_cml_statistics`
Get resource usage statistics (CPU, memory, node counts).
- **Inputs:** None.

### `get_cml_licensing_details`
Get the current licensing state of the CML server.
- **Inputs:** None.

---

## Users & Groups

### `get_cml_users`
List all users on the CML server.
- **Inputs:** None.

### `create_cml_user`
Create a new CML user.
- **Inputs:** `user` — object with `username`, `password`, and optional `fullname`, `email`, `roles`.

### `delete_cml_user`
Delete a user by UUID.
- **Inputs:** `user_id` (UUID).
- **Note:** UUID is obtained from `get_cml_users`.

### `get_cml_groups`
List all groups on the CML server.
- **Inputs:** None.

### `create_cml_group`
Create a new group.
- **Inputs:** `group` — object with `name` and optional `description`, `members`.

### `delete_cml_group`
Delete a group by UUID.
- **Inputs:** `group_id` (UUID).
- **Note:** UUID is obtained from `get_cml_groups`.

---

## Resolution Chain

When any tool is called with names, the following resolution happens internally:

```
lab_name  ──► resolve_lab_id()  ──► lid (UUID)
                                         │
node_label ──► resolve_node_id() ◄───────┤──► nid (UUID)
                                         │
node_a + node_b ──► resolve_link_id() ◄──┘──► link_id (UUID)
```

All resolution functions apply Unicode NFKC normalization and translate Unicode dash variants (en-dash, em-dash, etc.) to ASCII hyphens before comparing, preventing mismatches when an LLM substitutes lookalike characters.
