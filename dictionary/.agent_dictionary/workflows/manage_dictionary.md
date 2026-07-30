---
description: How to add, edit, or remove an asset prefix definition using the web-based dictionary tool.
---

# Manage Asset Dictionary

Current documentation refresh: 2026-04-28.

This workflow describes how administrators use the UI to securely update the active asset dictionary, which governs how metadata is applied to nameplates in the CMMS pipeline.

## Prerequisites
- Access to the Dictionary app running on its allocated port (usually `5000`).

---

## 1. Viewing the Dictionary

1. Open the dictionary dashboard in your browser.
2. The UI will render table containing all entries from the `mechanical_dictionary.py` file.
3. Because the system utilizes composite keys (e.g. `AHU|ME`), the UI will automatically split these and display the **Prefix (Tag)** and **Asset Type** as separate readable columns.

---

## 2. Adding a New Asset Definition

1. Click the **Add Asset** button.
2. Fill out the required parameters:
    - **UBC Tag Prefix**: The sequence letters found on nameplates (e.g. `RTU`, `CH`, `P-`).
    - **Asset Type**: The engineering discipline (e.g. `ME`, `EL`, `BF`).
    - **Asset Group**: Select from the dropdown (this queries PostgreSQL `qr_code_db` through the shared DB layer to prevent typos).
    - **Attribute Set**: (e.g., Mechanical, Tank, Electrical).
    - **Description**: The default readable label given to these assets.
3. Click **Save**.
4. The Flask backend will validate your input, generate a composite key (`PREFIX|TYPE`), check for duplicates, and rewrite the underlying `.py` file to disk immediately. 

---

## 3. Editing an Existing Asset

1. Find the target asset row and click the **Edit** icon.
2. The modal form will open, prepopulated with the existing values.
3. **Note on Migration**: If you are editing an older entry that is still using a legacy key (no pipe character, e.g. `AHU`), saving the edit will automatically convert its underlying dictionary key to the modern `TAG|TYPE` standard (`AHU|ME`).

---

## 4. Operational Impact

Once a dictionary save is committed, the updated Python dictionary is immediately active. 
- The next time the API Extraction script processes an image, it will import the fresh dictionary.
- The next time a Plate Review App refreshes, it will apply the new dictionary descriptions to new unreviewed assets.
