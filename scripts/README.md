# Utility Scripts

This directory contains utility scripts for one-time data organization tasks.

## Scripts

### `combine_property_conversations.py`
Combines all individual guest conversation files within each property's folder into a single file for that property.

**Usage:**
```bash
python3 scripts/combine_property_conversations.py
```

### `move_combined_files.py`
Moves combined conversation files from property subfolders to the main conversations directory.

**Usage:**
```bash
python3 scripts/move_combined_files.py
```

### `convert_to_conversational.py`
Converts conversation files from CSV format to conversational text format (if needed).

**Usage:**
```bash
python3 scripts/convert_to_conversational.py
```

## Note

These scripts were used during initial data organization. They may not be needed if you're using the sync system, which handles message organization automatically.
