---
name: No Cycles Bake
description: Never use Cycles bake, always use the Eevee camera bake.
trigger: always_on
---

# Baking Instructions
Never use Cycles bake. Always use the Eevee camera bake (via the _bake_material_via_live_camera function) for all procedural baking tasks, as Cycles baking is broken on the user's system and will result in black textures. The user also explicitly requested that the image live baking is properly shown (via the Eevee camera popup).
