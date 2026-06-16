bl_info = {
    "name": "Anime Shader Studio (Blender 5.1.2)",
    "author": "AP",
    "version": (18, 5, 0),
    "blender": (5, 1, 2),
    "location": "View3D > Sidebar > Anime Studio",
    "description": "Ultimate Anime Shader: SDF Face Maps, Anisotropic Hair, & Opaque GPU Packing.",
    "category": "Render",
}

import bpy
import os
import tempfile
import shutil

try:
    import numpy as np
except Exception:
    np = None

from bpy.props import StringProperty, IntProperty, BoolProperty, EnumProperty, PointerProperty, FloatProperty

# -------------------------------------------------------------------
# Configuration & Helpers
# -------------------------------------------------------------------

DEFAULT_SIZE = 1024
MASK_COLORSPACE = "Non-Color"
PACKED_ALPHA_MODE = "CHANNEL_PACKED"

def get_mat_name(base_name):
    return f"{base_name}_AnimeToon"

def ensure_dir(path: str):
    if path: os.makedirs(path, exist_ok=True)

def load_image_if_exists(filepath, non_color=False):
    if not filepath: return None
    try:
        path = bpy.path.abspath(filepath)
    except Exception:
        path = filepath
    if not path or not os.path.exists(path):
        return None
    try:
        img = bpy.data.images.load(path)
        if non_color:
            try: img.colorspace_settings.name = 'Non-Color'
            except Exception: pass
        return img
    except Exception:
        return None

def try_load_packed_maps_into_images(mat, images):
    # Prefer packed ILM/Detail images if they exist on disk or on the material
    scene = getattr(bpy.context, 'scene', None)
    out_dir = None
    if scene and hasattr(scene, 'genos_output_dir'):
        try: out_dir = bpy.path.abspath(scene.genos_output_dir)
        except Exception: out_dir = None

    base = material_base_name(mat)

    # Helper to resolve common filenames
    def find_packed(name_candidates, non_color=False):
        # Check material stored image first
        try:
            stored = getattr(mat, name_candidates.get('prop', ''), None)
            if stored and getattr(stored, 'has_data', False):
                try:
                    set_image_colorspace(stored, 'Non-Color' if non_color else 'sRGB')
                except: pass
                return stored
        except Exception:
            pass

        # Check workspace out_dir files
        if out_dir:
            for fname in name_candidates.get('files', []):
                path = os.path.join(out_dir, fname)
                img = load_image_if_exists(path, non_color=non_color)
                if img:
                    return img

        # Finally check any image already loaded with expected name
        for img in bpy.data.images:
            if img.name in name_candidates.get('names', []):
                try:
                    set_image_colorspace(img, 'Non-Color' if non_color else 'sRGB')
                except: pass
                return img
        return None

    # ILM packed candidates
    ilm_candidates = {
        'prop': 'genos_ilm_packed',
        'files': [f"{base}{getattr(scene, 'genos_exp_suf_ilm', '_ILM')}.png", f"{base}_ILM.png"],
        'names': [f"{base}_ILM", f"{base}{getattr(scene, 'genos_exp_suf_ilm', '_ILM')}.png"]
    }
    ilm_img = find_packed(ilm_candidates, non_color=True)
    if ilm_img:
        try: mat.genos_ilm_packed = ilm_img
        except Exception: pass
        for k in ("ilm_shadow", "ilm_emission", "ilm_spec", "ilm_rim"):
            images[k] = ilm_img

    detail_candidates = {
        'prop': 'genos_detail_packed',
        'files': [f"{base}{getattr(scene, 'genos_exp_suf_detail', '_Detail')}.png", f"{base}_Detail.png"],
        'names': [f"{base}_Detail", f"{base}{getattr(scene, 'genos_exp_suf_detail', '_Detail')}.png"]
    }
    det_img = find_packed(detail_candidates, non_color=True)
    if det_img:
        try: mat.genos_detail_packed = det_img
        except Exception: pass
        for k in ("detail_ao", "detail_curve", "detail_accent", "detail_emission"):
            images[k] = det_img
    # SDF map candidates (face shader)
    sdf_candidates = {
        'prop': 'genos_sdf_map',
        'files': [f"{base}{getattr(scene, 'genos_exp_suf_sdf', '_SDF')}.png", f"{base}_SDF.png"],
        'names': [f"{base}_SDF", f"{base}{getattr(scene, 'genos_exp_suf_sdf', '_SDF')}.png"]
    }
    sdf_img = find_packed(sdf_candidates, non_color=True)
    if sdf_img:
        images["sdf_map"] = sdf_img

def set_image_colorspace(img, colorspace):
    if img is None: return
    try: img.colorspace_settings.name = colorspace
    except Exception: pass

def set_channel_packed_alpha(img):
    if img is None: return
    try: img.alpha_mode = PACKED_ALPHA_MODE
    except Exception: pass

def configure_mask_image(img, *, packed=False):
    set_image_colorspace(img, MASK_COLORSPACE)
    if packed:
        set_channel_packed_alpha(img)

def fill_image_solid(img, color, w=DEFAULT_SIZE, h=DEFAULT_SIZE):
    if img is None: return
    
    # FIXED: Replaced 'not True' with proper memory check
    if not getattr(img, 'has_data', False) or img.size[0] == 0 or img.size[1] == 0:
        img.source = 'GENERATED'
        img.generated_width = w
        img.generated_height = h
        
    w, h = img.size
    if w == 0 or h == 0: return

    if np is not None:
        arr = np.empty((w * h, 4), dtype=np.float32)
        arr[:, 0] = color[0]
        arr[:, 1] = color[1]
        arr[:, 2] = color[2]
        arr[:, 3] = color[3] if len(color) > 3 else 1.0
        try:
            img.pixels.foreach_set(arr.ravel())
        except Exception:
            img.pixels[:] = arr.ravel().tolist()
    else:
        col4 = (color[0], color[1], color[2], color[3] if len(color) > 3 else 1.0)
        img.pixels[:] = list(col4) * (w * h)
    
    img.update()

def make_image(name, width, height, *, alpha=True, colorspace="sRGB", color=(0.5, 0.5, 0.5, 1.0)):
    img = bpy.data.images.get(name)
    
    if img is None:
        img = bpy.data.images.new(name=name, width=width, height=height, alpha=alpha, float_buffer=False)
        fill_image_solid(img, color, width, height)
    elif not getattr(img, 'has_data', False) or img.size[0] == 0:
        fill_image_solid(img, color, width, height)
        
    set_image_colorspace(img, colorspace)
    return img

def get_image_pixels(img):
    if img is None or not getattr(img, 'has_data', False) or img.size[0] == 0:
        return None
    try: _ = img.pixels[0]
    except: pass
    try:
        w, h = img.size
        expected_len = int(w * h * 4)
        # try efficient foreach_get into a preallocated list/array
        try:
            if np is not None:
                buf = np.empty((expected_len,), dtype=np.float32)
                img.pixels.foreach_get(buf)
                return buf.reshape((int(expected_len/4), 4))
            else:
                buf = [0.0] * expected_len
                img.pixels.foreach_get(buf)
                return buf
        except Exception:
            # fallback to slice read
            raw = img.pixels[:]
            pixel_count = len(raw) // 4
            valid_len = pixel_count * 4
            if np is not None:
                arr = np.array(raw[:valid_len], dtype=np.float32)
                return arr.reshape((pixel_count, 4))
            return raw[:valid_len]
    except:
        return None

def image_channel_array(img, channel="LUMA"):
    if img is None: return None
    px = get_image_pixels(img)
    if px is None: return None
    
    if np is not None and isinstance(px, np.ndarray):
        if channel == "R": return px[:, 0]
        if channel == "G": return px[:, 1]
        if channel == "B": return px[:, 2]
        if channel == "A": return px[:, 3]
        return px[:, 0] * 0.2126 + px[:, 1] * 0.7152 + px[:, 2] * 0.0722
    
    out = []
    it = iter(px)
    for r, g, b, a in zip(it, it, it, it):
        if channel == "R": out.append(r)
        elif channel == "G": out.append(g)
        elif channel == "B": out.append(b)
        elif channel == "A": out.append(a)
        else: out.append(r * 0.2126 + g * 0.7152 + b * 0.0722)
    return out

def get_resized_channel(src_img, target_w, target_h, channel):
    if not getattr(src_img, 'has_data', False) or src_img.size[0] == 0:
        return None
        
    if src_img.size[0] == target_w and src_img.size[1] == target_h:
        return image_channel_array(src_img, channel)
        
    if src_img.is_dirty:
        try: src_img.pack()
        except: pass
        
    temp = src_img.copy()
    try:
        temp.scale(target_w, target_h)
        temp.update()
        arr = image_channel_array(temp, channel)
    except Exception: arr = None
    finally: bpy.data.images.remove(temp)
    return arr

def pad_or_truncate(arr, target_length, default_val):
    if arr is None: 
        if np is not None: return np.full((target_length,), default_val, dtype=np.float32)
        return [default_val] * target_length
    
    if len(arr) == target_length: return arr
    
    if np is not None and isinstance(arr, np.ndarray):
        if len(arr) > target_length: return arr[:target_length]
        res = np.full((target_length,), default_val, dtype=np.float32)
        res[:len(arr)] = arr
        return res
    else:
        if len(arr) > target_length: return arr[:target_length]
        return arr + [default_val] * (target_length - len(arr))

def pack_rgba(dst_img, src_r=None, src_g=None, src_b=None, src_a=None, ch_r="LUMA", ch_g="LUMA", ch_b="LUMA", ch_a="LUMA", default_r=0.0, default_g=0.0, default_b=0.0, default_a=1.0):
    if dst_img is None: raise ValueError("Destination image is missing.")

    target_size = None
    for src in [src_r, src_g, src_b, src_a]:
        if src and getattr(src, 'has_data', False):
            try:
                _ = src.pixels[0]
                if src.size[0] > 0:
                    target_size = src.size[:]
                    break
            except Exception: pass

    if target_size is None:
        target_size = (DEFAULT_SIZE, DEFAULT_SIZE)

    target_w, target_h = target_size
    cs = dst_img.colorspace_settings.name

    if dst_img.size[0] != target_w or dst_img.size[1] != target_h:
        try:
            dst_img.scale(target_w, target_h)
            dst_img.update()
        except:
            dst_img.source = 'GENERATED'
            dst_img.generated_width = target_w
            dst_img.generated_height = target_h
            try: dst_img.update()
            except: pass

    try: _ = dst_img.pixels[0]
    except: pass

    px_cnt = target_w * target_h

    def get_ch(src, ch, def_val):
        try: _ = src.pixels[0]
        except: pass
        if src is None or not getattr(src, 'has_data', False) or src.size[0] == 0:
            return None
        return get_resized_channel(src, target_w, target_h, ch)

    r = pad_or_truncate(get_ch(src_r, ch_r, default_r), px_cnt, default_r)
    g = pad_or_truncate(get_ch(src_g, ch_g, default_g), px_cnt, default_g)
    b = pad_or_truncate(get_ch(src_b, ch_b, default_b), px_cnt, default_b)
    a = pad_or_truncate(get_ch(src_a, ch_a, default_a), px_cnt, default_a)

    if np is not None:
        packed = np.empty((px_cnt, 4), dtype=np.float32)
        packed[:,0]=r
        packed[:,1]=g
        packed[:,2]=b
        packed[:,3]=a
        try: dst_img.pixels.foreach_set(packed.ravel())
        except: dst_img.pixels[:] = packed.ravel().tolist()
    else:
        flat = []
        for i in range(px_cnt): flat.extend([float(r[i]), float(g[i]), float(b[i]), float(a[i])])
        dst_img.pixels[:] = flat

    try: dst_img.update()
    except: pass
    set_image_colorspace(dst_img, cs)
    set_channel_packed_alpha(dst_img)

def save_image(img, out_dir, filename=None):
    try: _ = img.pixels[0]
    except: pass
    if img is None or img.size[0] == 0: return None

    ensure_dir(out_dir)
    if filename is None: filename = img.name + ".png"
    path = os.path.join(out_dir, filename)

    # Try to get a reliable copy of the pixel buffer from the source image
    # Prefer the fast path via get_image_pixels (uses numpy/foreach_get when available)
    try:
        # pack source if dirty to prevent Blender from discarding the RAM buffer
        if getattr(img, 'is_dirty', False):
            try: img.pack()
            except: pass
    except Exception:
        pass

    px = get_image_pixels(img)
    if px is None:
        try:
            pixels = list(img.pixels)
        except Exception:
            return None
    else:
        # px may be a numpy array shaped (n,4) or a flat list
        if np is not None and isinstance(px, np.ndarray):
            pixels = px.ravel().tolist()
        else:
            # px from get_image_pixels for non-numpy path returns flat list
            pixels = list(px)

    w, h = img.size
    # create temp image and transfer pixels using fastest available API
    temp_img = bpy.data.images.new(name="TEMP_EXPORT", width=w, height=h, alpha=True, float_buffer=False)

    try:
        # attempt to use foreach_set for efficiency and reliability
        try:
            temp_img.pixels.foreach_set(pixels)
        except Exception:
            temp_img.pixels[:] = pixels
        temp_img.update()
    except Exception:
        # fallback: try setting pixels via Python list slice
        try:
            temp_img.pixels[:] = pixels
            temp_img.update()
        except Exception:
            bpy.data.images.remove(temp_img)
            return None

    try: temp_img.colorspace_settings.name = img.colorspace_settings.name
    except: pass

    # Preserve RGB under alpha by using CHANNEL_PACKED when available
    try: temp_img.alpha_mode = 'CHANNEL_PACKED'
    except: pass

    temp_img.filepath_raw = path
    temp_img.file_format = 'PNG'

    try: temp_img.save()
    except Exception:
        try: temp_img.save_render(filepath=path)
        except: pass

    bpy.data.images.remove(temp_img)
    return path

def make_node(nodes, node_type, name, location):
    node = nodes.new(node_type)
    node.name = name
    node.label = name
    node.location = location
    return node

def find_socket(collection, *names, index=None):
    for nm in names:
        if not nm: continue
        if nm in collection: return collection[nm]
        for sock in collection:
            if sock.name == nm: return sock
    if index is not None and len(collection) > index: return collection[index]
    if len(collection) > 0: return collection[0]
    raise KeyError(f"Socket not found; tried names={names}")

def link(links, out_socket, in_socket):
    links.new(out_socket, in_socket)

def set_active_image_node(mat, target_key):
    node_map = {
        "BASECOLOR": "BaseColor",
        "EMISSION_MAP": "Emission Map",
        "ILM_SHADOW": "ILM_Shadow",
        "ILM_EMISSION": "ILM_Emission",
        "ILM_SPEC": "ILM_Spec",
        "ILM_RIM": "ILM_Rim",
        "DETAIL_AO": "Detail_AO",
        "DETAIL_CURVE": "Detail_Curve",
        "DETAIL_ACCENT": "Detail_Accent",
        "DETAIL_EMISSION": "Detail_Emission"
    }
    name = node_map.get(target_key)
    if name and mat and mat.use_nodes:
        node = mat.node_tree.nodes.get(name)
        if node:
            mat.node_tree.nodes.active = node
            for n in mat.node_tree.nodes: n.select = False
            node.select = True
            return node
    return None

def active_mesh_object(context):
    obj = context.object
    if obj is None or obj.type != 'MESH': return None
    return obj

def _cycles_preferences():
    try:
        if 'cycles' not in bpy.context.preferences.addons:
            bpy.ops.preferences.addon_enable(module='cycles')
    except Exception:
        pass
    try:
        return bpy.context.preferences.addons['cycles'].preferences
    except Exception:
        return None

def _cycles_devices(prefs):
    if prefs is None:
        return []
    result = None
    try:
        result = prefs.get_devices()
    except Exception:
        pass
    try:
        devices = list(prefs.devices)
        if devices:
            return devices
    except Exception:
        pass
    devices = []
    if result:
        for group in result:
            if isinstance(group, (list, tuple)):
                devices.extend(group)
            else:
                devices.append(group)
    return devices

def enable_gpu_cycles(scene):
    prefs = _cycles_preferences()
    try:
        scene.render.engine = 'CYCLES'
    except Exception:
        return False

    devices = []
    has_gpu = False

    if prefs is not None:
        backend_order = ['OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL']
        if hasattr(prefs, 'compute_device_type'):
            for backend in backend_order:
                try:
                    prefs.compute_device_type = backend
                except Exception:
                    continue
                devices = _cycles_devices(prefs)
                has_gpu = any(getattr(d, 'type', 'CPU') != 'CPU' for d in devices)
                if has_gpu:
                    break
        else:
            devices = _cycles_devices(prefs)
            has_gpu = any(getattr(d, 'type', 'CPU') != 'CPU' for d in devices)

        for device in devices:
            try:
                device.use = getattr(device, 'type', 'CPU') != 'CPU' if has_gpu else True
            except Exception:
                pass

    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        try: cycles.device = 'GPU' if has_gpu else 'CPU'
        except Exception: pass
        try: cycles.use_denoising = False
        except Exception: pass
        try: cycles.use_adaptive_sampling = False
        except Exception: pass
        try: cycles.use_persistent_data = True
        except Exception: pass
    return has_gpu

def capture_bake_state(scene):
    state = {"engine": scene.render.engine}
    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        for attr in ("samples", "device", "use_denoising", "use_adaptive_sampling", "use_persistent_data"):
            if hasattr(cycles, attr):
                try: state[f"cycles.{attr}"] = getattr(cycles, attr)
                except Exception: pass
    bake = getattr(scene.render, "bake", None)
    if bake is not None:
        for attr in ("use_clear", "target", "save_mode", "margin", "margin_type", "use_selected_to_active", "use_cage"):
            if hasattr(bake, attr):
                try: state[f"bake.{attr}"] = getattr(bake, attr)
                except Exception: pass
    return state

def restore_bake_state(scene, state):
    try: scene.render.engine = state.get("engine", scene.render.engine)
    except Exception: pass
    cycles = getattr(scene, "cycles", None)
    bake = getattr(scene.render, "bake", None)
    for key, value in state.items():
        if key.startswith("cycles.") and cycles is not None:
            try: setattr(cycles, key.split(".", 1)[1], value)
            except Exception: pass
        elif key.startswith("bake.") and bake is not None:
            try: setattr(bake, key.split(".", 1)[1], value)
            except Exception: pass

def configure_internal_bake(scene, samples):
    enable_gpu_cycles(scene)
    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        try: cycles.samples = samples
        except Exception: pass
    bake = getattr(scene.render, "bake", None)
    if bake is not None:
        for attr, value in (
            ("target", 'IMAGE_TEXTURES'),
            ("save_mode", 'INTERNAL'),
            ("use_clear", False),
            ("use_selected_to_active", False),
            ("use_cage", False),
            ("margin", 16),
            ("margin_type", 'EXTEND'),
        ):
            if hasattr(bake, attr):
                try: setattr(bake, attr, value)
                except Exception: pass

def activate_bake_image_node(mat, node):
    if not mat or not mat.use_nodes or node is None:
        return
    for n in mat.node_tree.nodes:
        n.select = False
    node.select = True
    mat.node_tree.nodes.active = node

def bake_active_image(pass_type, *, margin=16, use_clear=False):
    result = bpy.ops.object.bake(
        type=pass_type,
        margin=margin,
        margin_type='EXTEND',
        use_selected_to_active=False,
        target='IMAGE_TEXTURES',
        save_mode='INTERNAL',
        use_clear=use_clear,
        use_cage=False
    )
    return 'FINISHED' in result

def execute_bake(context, mat, target_node_name, is_ao=False, *, colorspace=MASK_COLORSPACE, prefill_color=None, pack_after=True):
    obj = context.active_object
    if not obj or obj.type != 'MESH' or not obj.data.uv_layers: return False
    if not mat or not mat.use_nodes: return False

    node = mat.node_tree.nodes.get(target_node_name)
    if not node or not node.image: return False
    img = node.image

    if colorspace == MASK_COLORSPACE:
        configure_mask_image(img)
    else:
        set_image_colorspace(img, colorspace)
    fill_image_solid(img, prefill_color if prefill_color is not None else ((1.0, 1.0, 1.0, 1.0) if is_ao else (0.0, 0.0, 0.0, 1.0)))

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    context.view_layer.objects.active = obj

    activate_bake_image_node(mat, node)
    context.view_layer.update()

    bake_state = capture_bake_state(context.scene)

    configure_internal_bake(context.scene, 128 if is_ao else 64)

    success = False
    try:
        # For AO bakes, clear the target before baking to avoid residual black pixels
        success = bake_active_image('AO' if is_ao else 'EMIT', margin=16, use_clear=bool(is_ao))
        img.update()
        if pack_after:
            try: img.pack()
            except Exception: pass
    except Exception as e:
        print("Bake Exception:", e)
    finally:
        restore_bake_state(context.scene, bake_state)

    return success

def material_base_name(mat):
    if mat is None:
        return "AnimeToon"
    return mat.name.replace("_AnimeToon", "")

def material_node_image(mat, node_name, *, colorspace=None):
    if not mat or not mat.use_nodes:
        return None
    node = mat.node_tree.nodes.get(node_name)
    img = node.image if node and hasattr(node, "image") else None
    if img:
        try: img.update()
        except Exception: pass
        if colorspace:
            set_image_colorspace(img, colorspace)
    return img

def scene_texture_size():
    scene = getattr(bpy.context, "scene", None)
    if scene and hasattr(scene, "genos_texture_size"):
        try: return scene.genos_texture_size
        except Exception: pass
    return DEFAULT_SIZE

def packed_image_for_material(mat, suffix, color):
    base = material_base_name(mat)
    size = scene_texture_size()
    img = make_image(f"{base}_{suffix}", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=color)
    configure_mask_image(img, packed=True)
    return img

def pack_material_ilm(mat):
    if not mat or not mat.use_nodes:
        return None
    ilm_packed = packed_image_for_material(mat, "ILM", (0.5, 0.0, 0.0, 1.0))
    pack_rgba(
        ilm_packed,
        material_node_image(mat, "ILM_Shadow", colorspace=MASK_COLORSPACE),
        material_node_image(mat, "ILM_Emission", colorspace=MASK_COLORSPACE),
        material_node_image(mat, "ILM_Spec", colorspace=MASK_COLORSPACE),
        material_node_image(mat, "ILM_Rim", colorspace=MASK_COLORSPACE),
        default_r=0.5,
        default_g=0.0,
        default_b=0.0,
        default_a=1.0
    )
    configure_mask_image(ilm_packed, packed=True)
    try: mat.genos_ilm_packed = ilm_packed
    except Exception: pass
    return ilm_packed

def pack_material_detail(mat):
    if not mat or not mat.use_nodes:
        return None
    detail_packed = packed_image_for_material(mat, "Detail", (1.0, 0.0, 0.0, 1.0))
    pack_rgba(
        detail_packed,
        material_node_image(mat, "Detail_AO", colorspace=MASK_COLORSPACE),
        material_node_image(mat, "Detail_Curve", colorspace=MASK_COLORSPACE),
        material_node_image(mat, "Detail_Accent", colorspace=MASK_COLORSPACE),
        material_node_image(mat, "Detail_Emission", colorspace=MASK_COLORSPACE),
        default_r=1.0,
        default_g=0.0,
        default_b=0.0,
        default_a=1.0
    )
    configure_mask_image(detail_packed, packed=True)
    try: mat.genos_detail_packed = detail_packed
    except Exception: pass
    return detail_packed

def source_node_image(mat, node_name):
    if not mat or not mat.use_nodes:
        return None
    node = mat.node_tree.nodes.get(node_name)
    return node.image if node and hasattr(node, "image") else None

def ensure_source_image(mat, node_name, suffix, color, colorspace=MASK_COLORSPACE):
    img = source_node_image(mat, node_name)
    if img is None or not getattr(img, 'has_data', False) or img.size[0] == 0:
        img = make_image(f"{material_base_name(mat)}_{suffix}", scene_texture_size(), scene_texture_size(), alpha=True, colorspace=colorspace, color=color)
    set_image_colorspace(img, colorspace)
    return img

def make_bake_tex(nodes, mat, node_name, suffix, color, colorspace=MASK_COLORSPACE, location=(0, 0)):
    tex = nodes.new("ShaderNodeTexImage")
    tex.name = f"BakeSrc_{node_name}"
    tex.label = node_name
    tex.location = location
    tex.image = ensure_source_image(mat, node_name, suffix, color, colorspace)
    tex.interpolation = 'Linear'
    return tex

def set_map_range_smooth(node, from_min=0.45, from_max=0.55):
    node.interpolation_type = 'SMOOTHSTEP'
    node.inputs["From Min"].default_value = from_min
    node.inputs["From Max"].default_value = from_max
    node.inputs["To Min"].default_value = 0.0
    node.inputs["To Max"].default_value = 1.0
    try: node.clamp = True
    except Exception: pass

def image_is_nearly_black(img, threshold=0.004):
    px = get_image_pixels(img)
    if px is None:
        return True
    if np is not None and isinstance(px, np.ndarray):
        if len(px) == 0:
            return True
        return float(np.max(px[:, :3])) <= threshold
    max_rgb = 0.0
    for i in range(0, len(px), 4):
        max_rgb = max(max_rgb, float(px[i]), float(px[i + 1]), float(px[i + 2]))
        if max_rgb > threshold:
            return False
    return True

def set_image_alpha_from_source(dst_img, src_img, default_alpha=1.0):
    if dst_img is None:
        return
    w, h = dst_img.size
    px_cnt = int(w * h)
    alpha = pad_or_truncate(get_resized_channel(src_img, w, h, "A") if src_img else None, px_cnt, default_alpha)
    px = get_image_pixels(dst_img)
    if px is None:
        return
    if np is not None and isinstance(px, np.ndarray):
        out = px.copy()
        out[:, 3] = alpha
        try: dst_img.pixels.foreach_set(out.ravel())
        except Exception: dst_img.pixels[:] = out.ravel().tolist()
    else:
        flat = list(px)
        for i in range(px_cnt):
            flat[(i * 4) + 3] = float(alpha[i])
        dst_img.pixels[:] = flat
    dst_img.update()

def _cleanup_temp_datablocks(*, objects=(), meshes=(), cameras=(), lights=(), worlds=(), scenes=(), images=()):
    for coll, remover in (
        (objects, bpy.data.objects.remove),
        (meshes, bpy.data.meshes.remove),
        (cameras, bpy.data.cameras.remove),
        (lights, bpy.data.lights.remove),
        (worlds, bpy.data.worlds.remove),
        (scenes, bpy.data.scenes.remove),
        (images, bpy.data.images.remove),
    ):
        for datablock in coll:
            try:
                remover(datablock)
            except Exception:
                pass

def _make_uv_proxy_object(src_obj, *, name_prefix="GENOS_UV_PROXY"):
    if src_obj is None or src_obj.type != 'MESH':
        return None, None

    src_me = src_obj.data
    uv_layer = src_me.uv_layers.active if getattr(src_me, 'uv_layers', None) else None
    if uv_layer is None and getattr(src_me, 'uv_layers', None) and len(src_me.uv_layers) > 0:
        uv_layer = src_me.uv_layers[0]
    if uv_layer is None:
        return None, None

    proxy_mesh = bpy.data.meshes.new(f"{name_prefix}_MESH")
    verts = []
    faces = []
    for poly in src_me.polygons:
        face = []
        for li in poly.loop_indices:
            uv = uv_layer.data[li].uv
            verts.append((float(uv.x), float(uv.y), 0.0))
            face.append(len(verts) - 1)
        if len(face) >= 3:
            faces.append(face)

    if not faces:
        bpy.data.meshes.remove(proxy_mesh)
        return None, None

    proxy_mesh.from_pydata(verts, [], faces)
    proxy_mesh.update(calc_edges=True)
    
    # NEW: We MUST generate a UV map for the proxy plane so the material's textures map correctly.
    # Without this, all Image Textures return black, making the ShaderToRGB node output black.
    new_uv_layer = proxy_mesh.uv_layers.new(name="Proxy_UV")
    for poly in proxy_mesh.polygons:
        for loop_idx in poly.loop_indices:
            v_idx = proxy_mesh.loops[loop_idx].vertex_index
            new_uv_layer.data[loop_idx].uv = (verts[v_idx][0], verts[v_idx][1])

    proxy_obj = bpy.data.objects.new(f"{name_prefix}_OBJ", proxy_mesh)
    proxy_obj.location = (0.0, 0.0, 0.0)
    proxy_obj.rotation_euler = (0.0, 0.0, 0.0)
    proxy_obj.scale = (1.0, 1.0, 1.0)
    return proxy_obj, proxy_mesh


def _setup_temp_eevee_scene(scene_name="GENOS_TEMP_EEVEE_BAKE", size=1024):
    scene = bpy.data.scenes.new(scene_name)
    try:
        scene.render.engine = 'BLENDER_EEVEE_NEXT'
    except Exception:
        try:
            scene.render.engine = 'BLENDER_EEVEE'
        except Exception:
            pass
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    try:
        scene.render.film_transparent = True
    except Exception:
        pass
    try:
        scene.view_settings.view_transform = 'Standard'
    except Exception:
        pass
    try:
        scene.display_settings.display_device = 'sRGB'
    except Exception:
        pass

    world = bpy.data.worlds.new(f"{scene_name}_WORLD")
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    bg = nt.nodes.new('ShaderNodeBackground')
    bg.inputs[0].default_value = (0.0, 0.0, 0.0, 1.0)
    bg.inputs[1].default_value = 0.0
    wo = nt.nodes.new('ShaderNodeOutputWorld')
    nt.links.new(bg.outputs[0], wo.inputs[0])
    scene.world = world
    return scene, world


# -------------------------------------------------------------------
# CHANGED PARTS: Live EEVEE Rendering Viewport Logic
# -------------------------------------------------------------------

def _render_scene_to_file(context, scene, filepath):
    # Save the user's current workspace state
    orig_scene = context.window.scene
    orig_display_type = context.preferences.view.render_display_type
    
    try:
        # Force the UI to pop open a dedicated Render Window
        context.preferences.view.render_display_type = 'WINDOW'
        
        # Physically switch to the temporary scene to force EEVEE to initialize
        context.window.scene = scene
        context.view_layer.update()
        
        # Force dependency graph evaluation so EEVEE isn't rendering an empty frame
        _ = context.evaluated_depsgraph_get()
        
        # Render normally (this blocks Python execution but opens the window)
        scene.render.filepath = filepath
        bpy.ops.render.render(write_still=True)
        
    finally:
        # Safely restore the user's workspace
        context.window.scene = orig_scene
        context.preferences.view.render_display_type = orig_display_type
        
def _make_fullscreen_quad(name_prefix="GENOS_PACK_QUAD"):
    mesh = bpy.data.meshes.new(f"{name_prefix}_MESH")
    # Perfect 1x1 plane matching the Orthographic camera frame
    verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    faces = [(0, 1, 2, 3)]
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    
    uv_layer = mesh.uv_layers.new(name="Quad_UV")
    uv_layer.data[0].uv = (0.0, 0.0)
    uv_layer.data[1].uv = (1.0, 0.0)
    uv_layer.data[2].uv = (1.0, 1.0)
    uv_layer.data[3].uv = (0.0, 1.0)
    
    obj = bpy.data.objects.new(f"{name_prefix}_OBJ", mesh)
    return obj, mesh

def _render_material_via_camera(context, temp_mat, size, out_filepath, use_alpha=False):
    proxy_obj, proxy_mesh = _make_fullscreen_quad()
    if not proxy_obj: 
        return False

    scene = context.scene
    
    bake_col_name = "GENOS_LIVE_BAKE_DATA"
    if bake_col_name in bpy.data.collections:
        bake_col = bpy.data.collections[bake_col_name]
    else:
        bake_col = bpy.data.collections.new(bake_col_name)
        scene.collection.children.link(bake_col)
        
    for ob in list(bake_col.objects):
        bake_col.objects.unlink(ob)

    hidden_states = {}
    for ob in scene.objects:
        if ob.name != proxy_obj.name:
            hidden_states[ob] = ob.hide_render
            ob.hide_render = True

    bake_col.objects.link(proxy_obj)
    proxy_obj.hide_render = False
    proxy_obj.data.materials.clear()
    proxy_obj.data.materials.append(temp_mat)

    cam_data = bpy.data.cameras.new('GENOS_TEMP_CAM')
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = 1.0 
    cam_obj = bpy.data.objects.new('GENOS_TEMP_CAM', cam_data)
    cam_obj.location = (0.5, 0.5, 1.0) 
    bake_col.objects.link(cam_obj)
    cam_obj.hide_render = False
    
    orig_camera = scene.camera
    scene.camera = cam_obj

    # Store user rendering state
    orig_res_x = scene.render.resolution_x
    orig_res_y = scene.render.resolution_y
    orig_res_pct = scene.render.resolution_percentage
    orig_film_transp = scene.render.film_transparent
    orig_color_mode = scene.render.image_settings.color_mode
    orig_view_transform = scene.view_settings.view_transform
    orig_look = scene.view_settings.look
    
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100 
    scene.render.filepath = out_filepath
    
    # CRITICAL: Force Raw rendering. If use_alpha is False, exports solid RGB to prevent black transparency bugs
    scene.render.film_transparent = use_alpha
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA' if use_alpha else 'RGB'
    scene.view_settings.view_transform = 'Raw'
    scene.view_settings.look = 'None'

    orig_display = context.preferences.view.render_display_type
    context.preferences.view.render_display_type = 'WINDOW'
    context.view_layer.update() 
    
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

    success = True
    try:
        bpy.ops.render.render('EXEC_DEFAULT', write_still=True)
    except Exception as e:
        print('CAMERA_RENDER_ERROR:', e)
        success = False

    # Restore user rendering state
    context.preferences.view.render_display_type = orig_display
    scene.camera = orig_camera
    scene.render.resolution_x = orig_res_x
    scene.render.resolution_y = orig_res_y
    scene.render.resolution_percentage = orig_res_pct
    scene.render.film_transparent = orig_film_transp
    scene.render.image_settings.color_mode = orig_color_mode
    scene.view_settings.view_transform = orig_view_transform
    scene.view_settings.look = orig_look

    for ob, state in hidden_states.items():
        ob.hide_render = state

    bpy.data.objects.remove(proxy_obj)
    bpy.data.meshes.remove(proxy_mesh)
    bpy.data.objects.remove(cam_obj)
    bpy.data.cameras.remove(cam_data)

    return success

def _make_uv_proxy_object(context, src_obj, name_prefix="GENOS_UV_PROXY"):
    if src_obj is None or src_obj.type != 'MESH':
        print("GENOS ERROR: Active object is not a mesh.")
        return None, None

    # FORCE Object Mode (API fails to read mesh data if stuck in Edit Mode)
    orig_mode = context.mode
    if orig_mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Get evaluated mesh safely
    depsgraph = context.evaluated_depsgraph_get()
    eval_obj = src_obj.evaluated_get(depsgraph)
    src_me = eval_obj.to_mesh()

    if not getattr(src_me, 'uv_layers', None) or len(src_me.uv_layers) == 0:
        print("GENOS ERROR: Active object has no UV layers!")
        eval_obj.to_mesh_clear()
        if orig_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=orig_mode)
        return None, None

    uv_layer = src_me.uv_layers.active if src_me.uv_layers.active else src_me.uv_layers[0]

    proxy_mesh = bpy.data.meshes.new(f"{name_prefix}_MESH")
    verts = []
    faces = []
    for poly in src_me.polygons:
        face = []
        for li in poly.loop_indices:
            uv = uv_layer.data[li].uv
            verts.append((float(uv.x), float(uv.y), 0.0))
            face.append(len(verts) - 1)
        if len(face) >= 3:
            faces.append(face)

    eval_obj.to_mesh_clear()
    
    if orig_mode != 'OBJECT':
        try: bpy.ops.object.mode_set(mode=orig_mode)
        except: pass

    if not faces:
        print("GENOS ERROR: Proxy mesh generated 0 faces.")
        bpy.data.meshes.remove(proxy_mesh)
        return None, None

    proxy_mesh.from_pydata(verts, [], faces)
    proxy_mesh.update(calc_edges=True)
    
    new_uv_layer = proxy_mesh.uv_layers.new(name="Proxy_UV")
    for poly in proxy_mesh.polygons:
        for loop_idx in poly.loop_indices:
            v_idx = proxy_mesh.loops[loop_idx].vertex_index
            new_uv_layer.data[loop_idx].uv = (verts[v_idx][0], verts[v_idx][1])

    proxy_obj = bpy.data.objects.new(f"{name_prefix}_OBJ", proxy_mesh)
    proxy_obj.location = (0.0, 0.0, 0.0)
    return proxy_obj, proxy_mesh


def _render_uv_proxy_preview(context, mat, *, emission_only=False, size=1024):
    src_obj = context.active_object
    print(f"GENOS INFO: Starting live bake for {mat.name} at {size}x{size}...")

    # Pass context to proxy generator to handle modes
    proxy_obj, proxy_mesh = _make_uv_proxy_object(context, src_obj)
    if proxy_obj is None:
        print("GENOS ERROR: Proxy object creation failed. Check console for UV/Mesh errors.")
        return None, None

    scene = context.scene
    
    bake_col_name = "GENOS_LIVE_BAKE_DATA"
    if bake_col_name in bpy.data.collections:
        bake_col = bpy.data.collections[bake_col_name]
    else:
        bake_col = bpy.data.collections.new(bake_col_name)
        scene.collection.children.link(bake_col)

    for obj in list(bake_col.objects):
        bake_col.objects.unlink(obj)

    hidden_states = {}
    for obj in scene.objects:
        if obj.name not in [proxy_obj.name, 'GENOS_TEMP_CAM', 'GENOS_TEMP_SUN']:
            hidden_states[obj] = obj.hide_render
            obj.hide_render = True

    bake_col.objects.link(proxy_obj)
    proxy_obj.hide_render = False
    proxy_obj.data.materials.clear()
    proxy_obj.data.materials.append(mat)

    cam_data = bpy.data.cameras.new('GENOS_TEMP_CAM')
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = 1.0 
    cam_obj = bpy.data.objects.new('GENOS_TEMP_CAM', cam_data)
    cam_obj.location = (0.5, 0.5, 1.0)
    bake_col.objects.link(cam_obj)
    cam_obj.hide_render = False
    
    orig_camera = scene.camera
    scene.camera = cam_obj

    sun_data = bpy.data.lights.new('GENOS_TEMP_SUN', type='SUN')
    sun_data.energy = 4.0
    sun_obj = bpy.data.objects.new('GENOS_TEMP_SUN', sun_data)
    sun_obj.rotation_euler = (0.785398, 0.0, 0.785398)
    bake_col.objects.link(sun_obj)
    sun_obj.hide_render = False

    orig_res_x = scene.render.resolution_x
    orig_res_y = scene.render.resolution_y
    orig_res_pct = scene.render.resolution_percentage
    
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100 
    
    tmp_dir = tempfile.mkdtemp(prefix='genos_eevee_')
    tmp_path = os.path.join(tmp_dir, f'{mat.name}_preview.png')
    scene.render.filepath = tmp_path

    # Hand control back to Blender UI thread so the window actually spawns
    print("GENOS INFO: Spawning native render window via INVOKE_DEFAULT...")
    try:
        # INVOKE_DEFAULT forces the UI window open, bypassing thread locks
        bpy.ops.render.render('INVOKE_DEFAULT', write_still=True)
    except Exception as e:
        print('ACTIVE_SCENE_RENDER_ERROR:', e)

    # Note: Because INVOKE_DEFAULT is asynchronous, this function finishes BEFORE the render completes.
    # Therefore, cleanup of the camera/proxy mesh is disabled here so they don't get deleted mid-render.
    # You will see the GENOS_LIVE_BAKE_DATA collection stay in your scene.
    
    # Restore base scene settings so your actual workspace isn't ruined
    scene.camera = orig_camera
    scene.render.resolution_x = orig_res_x
    scene.render.resolution_y = orig_res_y
    scene.render.resolution_percentage = orig_res_pct

    for obj, state in hidden_states.items():
        obj.hide_render = state

    return None, None

def _smoothstep_array(edge0, edge1, value):
    if np is not None and isinstance(value, np.ndarray):
        t = np.clip((value - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)
    out = []
    denom = max(edge1 - edge0, 1e-6)
    for v in value:
        t = min(1.0, max(0.0, (float(v) - edge0) / denom))
        out.append(t * t * (3.0 - 2.0 * t))
    return out

def compose_preview_pixels(mat, dst_img, *, emission_only=False):
    if dst_img is None:
        return False
    w, h = dst_img.size
    px_cnt = int(w * h)

    def channel(node_name, suffix, color, ch="LUMA", colorspace=MASK_COLORSPACE, default=0.0):
        img = ensure_source_image(mat, node_name, suffix, color, colorspace)
        return pad_or_truncate(get_resized_channel(img, w, h, ch), px_cnt, default)

    base_r = channel("BaseColor", "BaseColor", (0.8, 0.8, 0.8, 1.0), "R", "sRGB", 0.8)
    base_g = channel("BaseColor", "BaseColor", (0.8, 0.8, 0.8, 1.0), "G", "sRGB", 0.8)
    base_b = channel("BaseColor", "BaseColor", (0.8, 0.8, 0.8, 1.0), "B", "sRGB", 0.8)
    base_a = channel("BaseColor", "BaseColor", (0.8, 0.8, 0.8, 1.0), "A", "sRGB", 1.0)
    em_r = channel("Emission Map", "EmissionMap", (0.0, 0.0, 0.0, 1.0), "R", "sRGB", 0.0)
    em_g = channel("Emission Map", "EmissionMap", (0.0, 0.0, 0.0, 1.0), "G", "sRGB", 0.0)
    em_b = channel("Emission Map", "EmissionMap", (0.0, 0.0, 0.0, 1.0), "B", "sRGB", 0.0)

    ilm_shadow = channel("ILM_Shadow", "ILM_ShadowSrc", (0.5, 0.5, 0.5, 1.0), default=0.5)
    ilm_emit = channel("ILM_Emission", "ILM_EmissionSrc", (0.0, 0.0, 0.0, 1.0), default=0.0)
    ilm_spec = channel("ILM_Spec", "ILM_SpecSrc", (0.0, 0.0, 0.0, 1.0), default=0.0)
    ilm_rim = channel("ILM_Rim", "ILM_RimSrc", (0.0, 0.0, 0.0, 1.0), default=0.0)
    det_ao = channel("Detail_AO", "Detail_AOSrc", (1.0, 1.0, 1.0, 1.0), default=1.0)
    det_curve = channel("Detail_Curve", "Detail_CurveSrc", (0.0, 0.0, 0.0, 1.0), default=0.0)
    det_accent = channel("Detail_Accent", "Detail_AccentSrc", (0.0, 0.0, 0.0, 1.0), default=0.0)
    det_emit = channel("Detail_Emission", "Detail_EmissionSrc", (0.0, 0.0, 0.0, 1.0), default=0.0)

    if np is not None and isinstance(base_r, np.ndarray):
        base_rgb = np.stack([base_r, base_g, base_b], axis=1)
        emission_rgb = np.stack([em_r, em_g, em_b], axis=1) * 10.0
        accent_rgb = np.array([1.0, 0.4, 0.4], dtype=np.float32)
        shadow_rgb = np.array([0.55, 0.55, 0.70], dtype=np.float32)
        rim_rgb = np.array([0.9, 0.9, 1.0], dtype=np.float32)

        shaded_base = base_rgb * det_ao[:, None]
        accented = shaded_base + (accent_rgb * det_accent[:, None])
        light_step = _smoothstep_array(0.45, 0.55, np.clip(0.85 + (ilm_shadow - 0.5), 0.0, 1.0))
        shadowed = accented * shadow_rgb
        lit = (shadowed * (1.0 - light_step[:, None])) + (accented * light_step[:, None])
        with_spec = lit + (np.ones((px_cnt, 3), dtype=np.float32) * (ilm_spec * 0.8)[:, None])
        with_lines = with_spec * np.clip(1.0 - det_curve, 0.0, 1.0)[:, None]
        glow_mask = np.clip(ilm_emit + det_emit, 0.0, 1.0)
        glow = emission_rgb + (accented * glow_mask[:, None])
        if emission_only:
            rgb = glow
        else:
            rgb = with_lines + (rim_rgb * (ilm_rim * 0.7)[:, None]) + glow
        out = np.empty((px_cnt, 4), dtype=np.float32)
        out[:, :3] = np.clip(rgb, 0.0, 1.0)
        out[:, 3] = np.clip(base_a, 0.0, 1.0)
        try: dst_img.pixels.foreach_set(out.ravel())
        except Exception: dst_img.pixels[:] = out.ravel().tolist()
    else:
        flat = []
        for i in range(px_cnt):
            shaded = [base_r[i] * det_ao[i], base_g[i] * det_ao[i], base_b[i] * det_ao[i]]
            accented = [shaded[0] + (1.0 * det_accent[i]), shaded[1] + (0.4 * det_accent[i]), shaded[2] + (0.4 * det_accent[i])]
            light_step = _smoothstep_array(0.45, 0.55, [min(1.0, max(0.0, 0.85 + (ilm_shadow[i] - 0.5)))])[0]
            shadowed = [accented[0] * 0.55, accented[1] * 0.55, accented[2] * 0.70]
            lit = [(shadowed[c] * (1.0 - light_step)) + (accented[c] * light_step) for c in range(3)]
            spec = min(1.0, max(0.0, ilm_spec[i] * 0.8))
            with_spec = [lit[c] + spec for c in range(3)]
            line = min(1.0, max(0.0, 1.0 - det_curve[i]))
            with_lines = [with_spec[c] * line for c in range(3)]
            glow_mask = min(1.0, max(0.0, ilm_emit[i] + det_emit[i]))
            glow = [(em_r[i] * 10.0) + (accented[0] * glow_mask), (em_g[i] * 10.0) + (accented[1] * glow_mask), (em_b[i] * 10.0) + (accented[2] * glow_mask)]
            if emission_only:
                rgb = glow
            else:
                rgb = [with_lines[0] + (0.9 * ilm_rim[i] * 0.7) + glow[0], with_lines[1] + (0.9 * ilm_rim[i] * 0.7) + glow[1], with_lines[2] + (1.0 * ilm_rim[i] * 0.7) + glow[2]]
            flat.extend([min(1.0, max(0.0, rgb[0])), min(1.0, max(0.0, rgb[1])), min(1.0, max(0.0, rgb[2])), min(1.0, max(0.0, base_a[i]))])
        dst_img.pixels[:] = flat
    set_image_colorspace(dst_img, "sRGB")
    dst_img.update()
    return True

def build_preview_bake_material(source_mat, target_img, *, emission_only=False):
    temp_mat = bpy.data.materials.new("TEMP_PREVIEW_APPEARANCE_BAKE")
    temp_mat.use_nodes = True
    nodes = temp_mat.node_tree.nodes
    links = temp_mat.node_tree.links
    nodes.clear()

    out = make_node(nodes, "ShaderNodeOutputMaterial", "Material Output", (1700, 0))
    emit = make_node(nodes, "ShaderNodeEmission", "Preview Appearance", (1450, 0))
    emit.inputs["Strength"].default_value = 1.0
    link(links, emit.outputs[0], out.inputs[0])

    target = make_node(nodes, "ShaderNodeTexImage", "GENOS_Preview_Bake_Target", (1450, -250))
    target.image = target_img
    nodes.active = target
    target.select = True

    base_tex = make_bake_tex(nodes, source_mat, "BaseColor", "BaseColor", (0.8, 0.8, 0.8, 1.0), "sRGB", (-1700, 500))
    emission_tex = make_bake_tex(nodes, source_mat, "Emission Map", "EmissionMap", (0.0, 0.0, 0.0, 1.0), "sRGB", (-1700, 300))
    ilm_shadow = make_bake_tex(nodes, source_mat, "ILM_Shadow", "ILM_ShadowSrc", (0.5, 0.5, 0.5, 1.0), MASK_COLORSPACE, (-1700, 100))
    ilm_emit = make_bake_tex(nodes, source_mat, "ILM_Emission", "ILM_EmissionSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, (-1700, 0))
    ilm_spec = make_bake_tex(nodes, source_mat, "ILM_Spec", "ILM_SpecSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, (-1700, -100))
    ilm_rim = make_bake_tex(nodes, source_mat, "ILM_Rim", "ILM_RimSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, (-1700, -200))
    det_ao = make_bake_tex(nodes, source_mat, "Detail_AO", "Detail_AOSrc", (1.0, 1.0, 1.0, 1.0), MASK_COLORSPACE, (-1700, -350))
    det_curve = make_bake_tex(nodes, source_mat, "Detail_Curve", "Detail_CurveSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, (-1700, -450))
    det_accent = make_bake_tex(nodes, source_mat, "Detail_Accent", "Detail_AccentSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, (-1700, -550))
    det_emit = make_bake_tex(nodes, source_mat, "Detail_Emission", "Detail_EmissionSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, (-1700, -650))

    ao_mul = make_node(nodes, "ShaderNodeMix", "Preview AO", (-1200, 450))
    ao_mul.data_type = 'RGBA'
    ao_mul.blend_type = 'MULTIPLY'
    find_socket(ao_mul.inputs, "Factor", "Fac").default_value = 1.0
    link(links, base_tex.outputs[0], find_socket(ao_mul.inputs, "A", "Color1"))
    link(links, det_ao.outputs[0], find_socket(ao_mul.inputs, "B", "Color2"))

    accent_add = make_node(nodes, "ShaderNodeMix", "Preview Accent", (-950, 450))
    accent_add.data_type = 'RGBA'
    accent_add.blend_type = 'ADD'
    link(links, det_accent.outputs[0], find_socket(accent_add.inputs, "Factor", "Fac"))
    link(links, find_socket(ao_mul.outputs, "Result", "Color"), find_socket(accent_add.inputs, "A", "Color1"))
    find_socket(accent_add.inputs, "B", "Color2").default_value = (1.0, 0.4, 0.4, 1.0)

    shadow_offset = make_node(nodes, "ShaderNodeMath", "Preview Shadow Offset", (-1200, 150))
    shadow_offset.operation = 'SUBTRACT'
    link(links, ilm_shadow.outputs[0], shadow_offset.inputs[0])
    shadow_offset.inputs[1].default_value = 0.5

    shadow_add = make_node(nodes, "ShaderNodeMath", "Preview Light Level", (-950, 150))
    shadow_add.operation = 'ADD'
    shadow_add.inputs[0].default_value = 0.85
    link(links, shadow_offset.outputs[0], shadow_add.inputs[1])

    shadow_step = make_node(nodes, "ShaderNodeMapRange", "Preview Shadow Step", (-700, 150))
    set_map_range_smooth(shadow_step)
    link(links, shadow_add.outputs[0], shadow_step.inputs["Value"])

    shadow_tint = make_node(nodes, "ShaderNodeMix", "Preview Shadow Tint", (-700, 450))
    shadow_tint.data_type = 'RGBA'
    shadow_tint.blend_type = 'MULTIPLY'
    find_socket(shadow_tint.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(accent_add.outputs, "Result", "Color"), find_socket(shadow_tint.inputs, "A", "Color1"))
    find_socket(shadow_tint.inputs, "B", "Color2").default_value = (0.55, 0.55, 0.70, 1.0)

    apply_shadow = make_node(nodes, "ShaderNodeMix", "Preview Apply Shadow", (-450, 400))
    apply_shadow.data_type = 'RGBA'
    apply_shadow.blend_type = 'MIX'
    link(links, shadow_step.outputs[0], find_socket(apply_shadow.inputs, "Factor", "Fac"))
    link(links, find_socket(shadow_tint.outputs, "Result", "Color"), find_socket(apply_shadow.inputs, "A", "Color1"))
    link(links, find_socket(accent_add.outputs, "Result", "Color"), find_socket(apply_shadow.inputs, "B", "Color2"))

    spec_gain = make_node(nodes, "ShaderNodeMath", "Preview Spec Gain", (-700, -100))
    spec_gain.operation = 'MULTIPLY'
    link(links, ilm_spec.outputs[0], spec_gain.inputs[0])
    spec_gain.inputs[1].default_value = 0.8

    spec_add = make_node(nodes, "ShaderNodeMix", "Preview Add Spec", (-200, 350))
    spec_add.data_type = 'RGBA'
    spec_add.blend_type = 'ADD'
    link(links, spec_gain.outputs[0], find_socket(spec_add.inputs, "Factor", "Fac"))
    link(links, find_socket(apply_shadow.outputs, "Result", "Color"), find_socket(spec_add.inputs, "A", "Color1"))
    find_socket(spec_add.inputs, "B", "Color2").default_value = (1.0, 1.0, 1.0, 1.0)

    line_inv = make_node(nodes, "ShaderNodeMath", "Preview Lineart Invert", (-450, -350))
    line_inv.operation = 'SUBTRACT'
    line_inv.inputs[0].default_value = 1.0
    link(links, det_curve.outputs[0], line_inv.inputs[1])

    line_mul = make_node(nodes, "ShaderNodeMix", "Preview Apply Lineart", (50, 300))
    line_mul.data_type = 'RGBA'
    line_mul.blend_type = 'MULTIPLY'
    find_socket(line_mul.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(spec_add.outputs, "Result", "Color"), find_socket(line_mul.inputs, "A", "Color1"))
    link(links, line_inv.outputs[0], find_socket(line_mul.inputs, "B", "Color2"))

    em_scale = make_node(nodes, "ShaderNodeMix", "Preview Emission Map", (-700, -650))
    em_scale.data_type = 'RGBA'
    em_scale.blend_type = 'MULTIPLY'
    find_socket(em_scale.inputs, "Factor", "Fac").default_value = 1.0
    link(links, emission_tex.outputs[0], find_socket(em_scale.inputs, "A", "Color1"))
    find_socket(em_scale.inputs, "B", "Color2").default_value = (10.0, 10.0, 10.0, 1.0)

    glow_mask = make_node(nodes, "ShaderNodeMath", "Preview Glow Mask", (-700, -450))
    glow_mask.operation = 'ADD'
    link(links, ilm_emit.outputs[0], glow_mask.inputs[0])
    link(links, det_emit.outputs[0], glow_mask.inputs[1])

    glow_color = make_node(nodes, "ShaderNodeMix", "Preview Masked Glow", (-450, -550))
    glow_color.data_type = 'RGBA'
    glow_color.blend_type = 'ADD'
    link(links, glow_mask.outputs[0], find_socket(glow_color.inputs, "Factor", "Fac"))
    find_socket(glow_color.inputs, "A", "Color1").default_value = (0.0, 0.0, 0.0, 1.0)
    link(links, find_socket(accent_add.outputs, "Result", "Color"), find_socket(glow_color.inputs, "B", "Color2"))

    total_emission = make_node(nodes, "ShaderNodeMix", "Preview Total Emission", (-200, -500))
    total_emission.data_type = 'RGBA'
    total_emission.blend_type = 'ADD'
    find_socket(total_emission.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(glow_color.outputs, "Result", "Color"), find_socket(total_emission.inputs, "A", "Color1"))
    link(links, find_socket(em_scale.outputs, "Result", "Color"), find_socket(total_emission.inputs, "B", "Color2"))

    if emission_only:
        link(links, find_socket(total_emission.outputs, "Result", "Color"), emit.inputs[0])
        return temp_mat

    rim_gain = make_node(nodes, "ShaderNodeMath", "Preview Rim Gain", (50, -150))
    rim_gain.operation = 'MULTIPLY'
    link(links, ilm_rim.outputs[0], rim_gain.inputs[0])
    rim_gain.inputs[1].default_value = 0.7

    rim_add = make_node(nodes, "ShaderNodeMix", "Preview Add Rim", (300, 250))
    rim_add.data_type = 'RGBA'
    rim_add.blend_type = 'ADD'
    link(links, rim_gain.outputs[0], find_socket(rim_add.inputs, "Factor", "Fac"))
    link(links, find_socket(line_mul.outputs, "Result", "Color"), find_socket(rim_add.inputs, "A", "Color1"))
    find_socket(rim_add.inputs, "B", "Color2").default_value = (0.9, 0.9, 1.0, 1.0)

    final_add = make_node(nodes, "ShaderNodeMix", "Preview Final Color", (600, 100))
    final_add.data_type = 'RGBA'
    final_add.blend_type = 'ADD'
    find_socket(final_add.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(rim_add.outputs, "Result", "Color"), find_socket(final_add.inputs, "A", "Color1"))
    link(links, find_socket(total_emission.outputs, "Result", "Color"), find_socket(final_add.inputs, "B", "Color2"))
    link(links, find_socket(final_add.outputs, "Result", "Color"), emit.inputs[0])
    return temp_mat

def bake_preview_texture(context, mat, *, emission_only=False):
    obj = context.active_object
    if not obj or obj.type != 'MESH' or not mat:
        return None

    size = scene_texture_size()
    suffix = "BakedPreviewEmission" if emission_only else "BakedPreview"
    fallback_img = make_image(f"{material_base_name(mat)}_{suffix}", size, size, alpha=True, colorspace="sRGB", color=(0.0, 0.0, 0.0, 1.0))
    set_image_colorspace(fallback_img, "sRGB")

    img = None
    cleanup = None
    try:
        img, cleanup = _render_uv_proxy_preview(context, mat, emission_only=emission_only, size=size)
    except Exception as e:
        print('EEVEE_PROXY_RENDER_FAILED:', e)

    if img is None:
        try:
            compose_preview_pixels(mat, fallback_img, emission_only=emission_only)
            fallback_img['genos_bake_source'] = 'preview_composite_fallback'
            try:
                fallback_img.pack()
            except Exception:
                pass
            return fallback_img
        except Exception as e:
            print('PREVIEW_FALLBACK_FAILED:', e)
            return None

    # Ensure the rendered image is what gets exported, not the raw Render Result placeholder.
    try:
        rendered = img.copy()
        rendered.name = fallback_img.name
        rendered.scale(size, size)
        rendered.update()
        rendered.colorspace_settings.name = 'sRGB'
    except Exception:
        rendered = img

    try:
        rendered['genos_bake_source'] = 'eevee_uv_proxy_render'
    except Exception:
        pass

    try:
        set_image_alpha_from_source(rendered, source_node_image(mat, 'BaseColor'), 1.0)
    except Exception:
        pass

    try:
        rendered.pack()
    except Exception:
        pass

    # Clean up temp scene/data and temp file directory.
    if cleanup:
        try:
            _cleanup_temp_datablocks(
                objects=(cleanup.get('proxy_obj'), cleanup.get('cam_obj'), cleanup.get('sun_obj'), cleanup.get('area_obj')),
                meshes=(cleanup.get('proxy_mesh'),),
                cameras=(cleanup.get('cam_data'),),
                lights=(cleanup.get('sun_data'), cleanup.get('area_data')),
                worlds=(cleanup.get('world'),),
                scenes=(cleanup.get('scene'),),
            )
        except Exception as e:
            print('TEMP_CLEANUP_ERROR:', e)
        try:
            shutil.rmtree(cleanup.get('tmp_dir'), ignore_errors=True)
        except Exception:
            pass

    return rendered

def build_preview_material(mat, images):
    mat.use_nodes = True
    mat["is_anime_toon"] = True 
    # Read shader type (DEFAULT, FACE, HAIR)
    shader_type = mat.get("genos_shader_type", "DEFAULT")

    try:
        mat.blend_method = 'CLIP'
        mat.shadow_method = 'CLIP' 
        mat.alpha_threshold = 0.5
        mat.show_transparent_back = False 
    except AttributeError: pass

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear() 
    
    base = mat.name.replace("_AnimeToon", "")
    shader_type = mat.get("genos_shader_type", "DEFAULT")

    def get_safe(key, suffix, color, cs="sRGB", use_alpha=True):
        img = images.get(key)
        if img is None or not getattr(img, 'has_data', False) or img.size[0] == 0:
            img = make_image(f"{base}_{suffix}", DEFAULT_SIZE, DEFAULT_SIZE, alpha=use_alpha, colorspace=cs, color=color)
        set_image_colorspace(img, cs)
        return img

    safe_basecolor = get_safe("basecolor", "BaseColor", (0.8, 0.8, 0.8, 1.0), "sRGB", True)
    safe_emission = get_safe("emission_map", "EmissionMap", (0.0, 0.0, 0.0, 1.0), "sRGB", True)
    safe_shadow = get_safe("ilm_shadow", "ILM_ShadowSrc", (0.5, 0.5, 0.5, 1.0), MASK_COLORSPACE, True)
    safe_ilm_emit = get_safe("ilm_emission", "ILM_EmissionSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_spec = get_safe("ilm_spec", "ILM_SpecSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_rim = get_safe("ilm_rim", "ILM_RimSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_ao = get_safe("detail_ao", "Detail_AOSrc", (1.0, 1.0, 1.0, 1.0), MASK_COLORSPACE, True) 
    safe_curve = get_safe("detail_curve", "Detail_CurveSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_accent = get_safe("detail_accent", "Detail_AccentSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_det_emit = get_safe("detail_emission", "Detail_EmissionSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)

    output = make_node(nodes, "ShaderNodeOutputMaterial", "Material Output", (3000, 0))
    mix_shader = make_node(nodes, "ShaderNodeMixShader", "Alpha Blend", (2700, 0))
    emission_out = make_node(nodes, "ShaderNodeEmission", "Anime Terminal Output", (2400, 0))
    transparent_bsdf = make_node(nodes, "ShaderNodeBsdfTransparent", "Transparency", (2400, -300))
    
    strength_val = make_node(nodes, "ShaderNodeValue", "Global Emission Strength", (2400, -150))
    strength_val.outputs[0].default_value = 2.0 
    
    link(links, strength_val.outputs[0], emission_out.inputs["Strength"])
    link(links, transparent_bsdf.outputs[0], mix_shader.inputs[1]) 
    link(links, emission_out.outputs[0], mix_shader.inputs[2])     
    link(links, mix_shader.outputs[0], output.inputs[0])

    base_tex = make_node(nodes, "ShaderNodeTexImage", "BaseColor", (-1500, 400))
    base_tex.image = safe_basecolor
    base_tex.interpolation = 'Smart'
    
    alpha_clip_gate = make_node(nodes, "ShaderNodeMath", "Alpha Failsafe", (2300, 200))
    alpha_clip_gate.operation = 'GREATER_THAN'
    alpha_clip_gate.inputs[1].default_value = 0.1
    link(links, base_tex.outputs[1], alpha_clip_gate.inputs[0])
    link(links, alpha_clip_gate.outputs[0], mix_shader.inputs[0])

    emission_map = make_node(nodes, "ShaderNodeTexImage", "Emission Map", (-1500, 300))
    emission_map.image = safe_emission
    emission_map.interpolation = 'Smart'

    scene = bpy.context.scene if bpy.context else None
    emission_channel = getattr(scene, "genos_emission_channel", "RGBA")
    emission_source = emission_map
    if emission_channel != "RGBA":
        sep = make_node(nodes, "ShaderNodeSeparateColor", "Emission Channel Split", (-1300, 250))
        link(links, emission_map.outputs[0], sep.inputs[0])
        comb = make_node(nodes, "ShaderNodeCombineColor", "Emission Channel Combine", (-1000, 300))
        if emission_channel == "A":
            link(links, sep.outputs[3], comb.inputs[0])
            link(links, sep.outputs[3], comb.inputs[1])
            link(links, sep.outputs[3], comb.inputs[2])
        else:
            channel_map = {"R": 0, "G": 1, "B": 2}
            idx = channel_map.get(emission_channel, 0)
            link(links, sep.outputs[idx], comb.inputs[0])
            link(links, sep.outputs[idx], comb.inputs[1])
            link(links, sep.outputs[idx], comb.inputs[2])
        emission_source = comb

    ilm_shadow = make_node(nodes, "ShaderNodeTexImage", "ILM_Shadow", (-1500, 150))
    ilm_shadow.image = safe_shadow
    ilm_shadow.interpolation = 'Linear'

    ilm_emission = make_node(nodes, "ShaderNodeTexImage", "ILM_Emission", (-1500, 50))
    ilm_emission.image = safe_ilm_emit
    ilm_emission.interpolation = 'Linear'

    ilm_spec = make_node(nodes, "ShaderNodeTexImage", "ILM_Spec", (-1500, -50))
    ilm_spec.image = safe_spec
    ilm_spec.interpolation = 'Linear'

    ilm_rim = make_node(nodes, "ShaderNodeTexImage", "ILM_Rim", (-1500, -150))
    ilm_rim.image = safe_rim
    ilm_rim.interpolation = 'Linear'

    det_ao = make_node(nodes, "ShaderNodeTexImage", "Detail_AO", (-1500, -300))
    det_ao.image = safe_ao
    det_ao.interpolation = 'Linear'

    det_curve = make_node(nodes, "ShaderNodeTexImage", "Detail_Curve", (-1500, -400))
    det_curve.image = safe_curve
    det_curve.interpolation = 'Linear'

    det_accent = make_node(nodes, "ShaderNodeTexImage", "Detail_Accent", (-1500, -500))
    det_accent.image = safe_accent
    det_accent.interpolation = 'Linear'

    det_emit = make_node(nodes, "ShaderNodeTexImage", "Detail_Emission", (-1500, -600))
    det_emit.image = safe_det_emit
    det_emit.interpolation = 'Linear'

    # Normal Map Support
    normal_map_node = make_node(nodes, "ShaderNodeNormalMap", "Normal Map", (-1500, -800))
    if images.get("normal_map"):
        normal_tex = make_node(nodes, "ShaderNodeTexImage", "Normal_Tex", (-1800, -800))
        normal_tex.image = images.get("normal_map")
        try: normal_tex.image.colorspace_settings.name = 'Non-Color'
        except: pass
        link(links, normal_tex.outputs[0], normal_map_node.inputs["Color"])

    # Geometry Injection
    ao_mul = make_node(nodes, "ShaderNodeMix", "Apply Global AO", (-1200, 400))
    ao_mul.data_type = 'RGBA'
    ao_mul.blend_type = 'MULTIPLY'
    link(links, base_tex.outputs[0], find_socket(ao_mul.inputs, "A", "Color1"))
    link(links, det_ao.outputs[0], find_socket(ao_mul.inputs, "B", "Color2"))
    find_socket(ao_mul.inputs, "Factor", "Fac").default_value = 1.0

    accent_add = make_node(nodes, "ShaderNodeMix", "Detail Accent", (-900, 400))
    accent_add.data_type = 'RGBA'
    accent_add.blend_type = 'ADD'
    link(links, det_accent.outputs[0], find_socket(accent_add.inputs, "Factor", "Fac"))
    link(links, find_socket(ao_mul.outputs, "Result", "Color"), find_socket(accent_add.inputs, "A", "Color1"))
    find_socket(accent_add.inputs, "B", "Color2").default_value = (1.0, 0.4, 0.4, 1.0) 

    # --- LIGHTING PIPELINE ---
    diffuse = make_node(nodes, "ShaderNodeBsdfDiffuse", "Scene Light Capture", (-1200, -900))
    link(links, normal_map_node.outputs["Normal"], diffuse.inputs["Normal"])
    
    s2rgb = make_node(nodes, "ShaderNodeShaderToRGB", "Shader to RGB", (-900, -900))
    bw_light = make_node(nodes, "ShaderNodeRGBToBW", "Light Intensity", (-700, -900))
    link(links, diffuse.outputs[0], s2rgb.inputs[0])
    link(links, s2rgb.outputs[0], bw_light.inputs[0])

    shadow_offset = make_node(nodes, "ShaderNodeMath", "Normalize ILM.R", (-800, -1000))
    shadow_offset.operation = 'SUBTRACT'
    link(links, ilm_shadow.outputs[0], shadow_offset.inputs[0])
    shadow_offset.inputs[1].default_value = 0.5

    shadow_add = make_node(nodes, "ShaderNodeMath", "Apply Shadow Bias", (-500, -900))
    shadow_add.operation = 'ADD'
    link(links, bw_light.outputs[0], shadow_add.inputs[0])
    link(links, shadow_offset.outputs[0], shadow_add.inputs[1]) 

    # Use SDF-driven shadow thresholds for face shaders
    if shader_type == 'FACE':
        sdf_tex = make_node(nodes, "ShaderNodeTexImage", "SDF Map", (-700, -1100))
        if images.get("sdf_map"):
            sdf_tex.image = images.get("sdf_map")
            try: sdf_tex.image.colorspace_settings.name = 'Non-Color'
            except: pass

        sdf_min = make_node(nodes, "ShaderNodeMath", "SDF Min Edge", (-500, -1050))
        sdf_min.operation = 'SUBTRACT'
        link(links, sdf_tex.outputs[0], sdf_min.inputs[0])
        sdf_min.inputs[1].default_value = 0.05

        sdf_max = make_node(nodes, "ShaderNodeMath", "SDF Max Edge", (-500, -1150))
        sdf_max.operation = 'ADD'
        link(links, sdf_tex.outputs[0], sdf_max.inputs[0])
        sdf_max.inputs[1].default_value = 0.05

        shadow_step = make_node(nodes, "ShaderNodeMapRange", "SDF Shadow Edge", (-300, -900))
        shadow_step.interpolation_type = 'SMOOTHSTEP'
        link(links, shadow_add.outputs[0], shadow_step.inputs[0])
        link(links, sdf_min.outputs[0], shadow_step.inputs[1])
        link(links, sdf_max.outputs[0], shadow_step.inputs[2])
    else:
        shadow_step = make_node(nodes, "ShaderNodeMapRange", "Smooth Shadow Edge", (-300, -900))
        shadow_step.interpolation_type = 'SMOOTHSTEP'
        shadow_step.inputs["From Min"].default_value = 0.45
        shadow_step.inputs["From Max"].default_value = 0.55
        link(links, shadow_add.outputs[0], shadow_step.inputs["Value"])

    # -------------------------------------------------------------
    # HAIR SPECULAR PIPELINE INJECTION
    # -------------------------------------------------------------
    if shader_type == 'HAIR':
        glossy = make_node(nodes, "ShaderNodeBsdfAnisotropic", "Hair Specular Capture", (-1200, -700))
        # Fallback to Glossy if Anisotropic node doesn't exist (Blender 4.0+)
        if glossy is None:
            glossy = make_node(nodes, "ShaderNodeBsdfGlossy", "Hair Specular Capture", (-1200, -700))
            try:
                glossy.inputs["Roughness"].default_value = 0.2
                glossy.inputs["Anisotropy"].default_value = 0.8
                glossy.inputs["Rotation"].default_value = 0.25
            except: pass
        else:
            try:
                glossy.inputs["Roughness"].default_value = 0.2
                glossy.inputs["Anisotropy"].default_value = 0.8
                glossy.inputs["Rotation"].default_value = 0.25
            except: pass
    else:
        glossy = make_node(nodes, "ShaderNodeBsdfGlossy", "Specular Capture", (-1200, -700))
        try: glossy.inputs["Roughness"].default_value = 0.05
        except: pass
        
    link(links, normal_map_node.outputs["Normal"], glossy.inputs["Normal"])
    
    gs2rgb = make_node(nodes, "ShaderNodeShaderToRGB", "Glossy to RGB", (-900, -700))
    gbw_light = make_node(nodes, "ShaderNodeRGBToBW", "Glossy Intensity", (-700, -700))
    link(links, glossy.outputs[0], gs2rgb.inputs[0])
    link(links, gs2rgb.outputs[0], gbw_light.inputs[0])

    spec_thresh = make_node(nodes, "ShaderNodeValue", "Specular Threshold", (-900, -600))
    spec_thresh.outputs[0].default_value = 0.6  
    
    spec_smooth = make_node(nodes, "ShaderNodeMath", "Spec Smooth Edge", (-700, -600))
    spec_smooth.operation = 'ADD'
    spec_smooth.inputs[1].default_value = 0.1
    link(links, spec_thresh.outputs[0], spec_smooth.inputs[0])

    glossy_step = make_node(nodes, "ShaderNodeMapRange", "Smooth Spec Edge", (-500, -700))
    glossy_step.interpolation_type = 'SMOOTHSTEP'
    link(links, spec_thresh.outputs[0], glossy_step.inputs[1])
    link(links, spec_smooth.outputs[0], glossy_step.inputs[2])
    link(links, gbw_light.outputs[0], glossy_step.inputs[0])

    glossy_mask = make_node(nodes, "ShaderNodeMath", "Mask Spec with ILM.B", (-300, -700))
    glossy_mask.operation = 'MULTIPLY'
    link(links, glossy_step.outputs[0], glossy_mask.inputs[0])
    link(links, ilm_spec.outputs[0], glossy_mask.inputs[1]) 

    # --- COLOR MIXING ---
    shadow_tint = make_node(nodes, "ShaderNodeMix", "Shadow Color", (-200, 100))
    shadow_tint.data_type = 'RGBA'
    shadow_tint.blend_type = 'MULTIPLY'
    find_socket(shadow_tint.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(accent_add.outputs, "Result", "Color"), find_socket(shadow_tint.inputs, "A", "Color1"))
    find_socket(shadow_tint.inputs, "B", "Color2").default_value = (0.55, 0.55, 0.70, 1.0) 

    apply_shadow = make_node(nodes, "ShaderNodeMix", "Apply Shading", (300, 0))
    apply_shadow.data_type = 'RGBA'
    apply_shadow.blend_type = 'MIX'
    link(links, shadow_step.outputs[0], find_socket(apply_shadow.inputs, "Factor", "Fac"))
    link(links, find_socket(shadow_tint.outputs, "Result", "Color"), find_socket(apply_shadow.inputs, "A", "Color1")) 
    link(links, find_socket(accent_add.outputs, "Result", "Color"), find_socket(apply_shadow.inputs, "B", "Color2")) 

    spec_add = make_node(nodes, "ShaderNodeMix", "Add Dynamic Specular", (600, 0))
    spec_add.data_type = 'RGBA'
    spec_add.blend_type = 'ADD'
    link(links, glossy_mask.outputs[0], find_socket(spec_add.inputs, "Factor", "Fac")) 
    link(links, find_socket(apply_shadow.outputs, "Result", "Color"), find_socket(spec_add.inputs, "A", "Color1"))
    find_socket(spec_add.inputs, "B", "Color2").default_value = (1.0, 1.0, 1.0, 1.0)

    # --- INNER LINEART ---
    line_str = make_node(nodes, "ShaderNodeValue", "Inner Lineart Strength", (600, 200))
    line_str.outputs[0].default_value = 1.0
    
    line_mul = make_node(nodes, "ShaderNodeMath", "Scale Lineart", (800, 200))
    line_mul.operation = 'MULTIPLY'
    link(links, det_curve.outputs[0], line_mul.inputs[0])
    link(links, line_str.outputs[0], line_mul.inputs[1])

    line_inv = make_node(nodes, "ShaderNodeMath", "Invert Lineart", (1000, 200))
    line_inv.operation = 'SUBTRACT'
    line_inv.inputs[0].default_value = 1.0
    link(links, line_mul.outputs[0], line_inv.inputs[1])

    apply_lineart = make_node(nodes, "ShaderNodeMix", "Apply Lineart", (900, 0))
    apply_lineart.data_type = 'RGBA'
    apply_lineart.blend_type = 'MULTIPLY'
    find_socket(apply_lineart.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(spec_add.outputs, "Result", "Color"), find_socket(apply_lineart.inputs, "A", "Color1"))
    link(links, line_inv.outputs[0], find_socket(apply_lineart.inputs, "B", "Color2"))

    # --- EMISSION STACK ---
    map_strength_val = make_node(nodes, "ShaderNodeValue", "Emission Map Strength", (1000, -350))
    map_strength_val.outputs[0].default_value = 10.0
    
    map_strength_mul = make_node(nodes, "ShaderNodeMix", "Scale Emission Map", (1200, -350))
    map_strength_mul.data_type = 'RGBA'
    map_strength_mul.blend_type = 'MULTIPLY'
    find_socket(map_strength_mul.inputs, "Factor", "Fac").default_value = 1.0
    link(links, emission_source.outputs[0], find_socket(map_strength_mul.inputs, "A", "Color1"))
    link(links, map_strength_val.outputs[0], find_socket(map_strength_mul.inputs, "B", "Color2"))

    combo_emit = make_node(nodes, "ShaderNodeMath", "Combine Glow Masks", (1000, -500))
    combo_emit.operation = 'ADD'
    link(links, ilm_emission.outputs[0], combo_emit.inputs[0]) 
    link(links, det_emit.outputs[0], combo_emit.inputs[1])

    emit_add_masks = make_node(nodes, "ShaderNodeMix", "Add Masked Base Glow", (1200, -500))
    emit_add_masks.data_type = 'RGBA'
    emit_add_masks.blend_type = 'ADD'
    link(links, combo_emit.outputs[0], find_socket(emit_add_masks.inputs, "Factor", "Fac"))
    find_socket(emit_add_masks.inputs, "A", "Color1").default_value = (0.0, 0.0, 0.0, 1.0)
    link(links, find_socket(accent_add.outputs, "Result", "Color"), find_socket(emit_add_masks.inputs, "B", "Color2"))

    total_emission = make_node(nodes, "ShaderNodeMix", "Total Raw Emission", (1400, -350))
    total_emission.data_type = 'RGBA'
    total_emission.blend_type = 'ADD'
    find_socket(total_emission.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(emit_add_masks.outputs, "Result", "Color"), find_socket(total_emission.inputs, "A", "Color1"))
    link(links, find_socket(map_strength_mul.outputs, "Result", "Color"), find_socket(total_emission.inputs, "B", "Color2"))

    emit_shadow_toggle = make_node(nodes, "ShaderNodeValue", "Emission Receives Shadows", (1300, -600))
    emit_shadow_toggle.outputs[0].default_value = 0.0 

    emit_shadow_mix = make_node(nodes, "ShaderNodeMix", "Shadowed Emission Blend", (1500, -600))
    emit_shadow_mix.data_type = 'FLOAT'
    link(links, emit_shadow_toggle.outputs[0], emit_shadow_mix.inputs[0])
    emit_shadow_mix.inputs[2].default_value = 1.0 
    link(links, shadow_step.outputs[0], emit_shadow_mix.inputs[3]) 

    emit_shadow_tint = make_node(nodes, "ShaderNodeMix", "Emissive Shadow Tint Color", (1500, -750))
    emit_shadow_tint.data_type = 'RGBA'
    emit_shadow_tint.blend_type = 'MULTIPLY'
    find_socket(emit_shadow_tint.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(total_emission.outputs, "Result", "Color"), find_socket(emit_shadow_tint.inputs, "A", "Color1"))
    find_socket(emit_shadow_tint.inputs, "B", "Color2").default_value = (0.2, 0.05, 0.5, 1.0)

    shade_emission_final = make_node(nodes, "ShaderNodeMix", "Apply Shadowed Emission", (1700, -350))
    shade_emission_final.data_type = 'RGBA'
    shade_emission_final.blend_type = 'MIX'
    link(links, emit_shadow_mix.outputs[0], find_socket(shade_emission_final.inputs, "Factor", "Fac")) 
    link(links, find_socket(emit_shadow_tint.outputs, "Result", "Color"), find_socket(shade_emission_final.inputs, "A", "Color1")) 
    link(links, find_socket(total_emission.outputs, "Result", "Color"), find_socket(shade_emission_final.inputs, "B", "Color2")) 

    fresnel = make_node(nodes, "ShaderNodeFresnel", "Rim Fresnel", (1500, -800))
    fresnel.inputs["IOR"].default_value = 1.1
    link(links, normal_map_node.outputs["Normal"], fresnel.inputs["Normal"])
    
    rim_mask = make_node(nodes, "ShaderNodeMath", "Mask Rim", (1700, -800))
    rim_mask.operation = 'MULTIPLY'
    link(links, fresnel.outputs[0], rim_mask.inputs[0])
    link(links, ilm_rim.outputs[0], rim_mask.inputs[1]) 
    
    rim_step = make_node(nodes, "ShaderNodeMapRange", "Smooth Rim Edge", (1900, -800))
    rim_step.interpolation_type = 'SMOOTHSTEP'
    rim_step.inputs["From Min"].default_value = 0.45
    rim_step.inputs["From Max"].default_value = 0.55
    link(links, rim_mask.outputs[0], rim_step.inputs["Value"])

    rim_add = make_node(nodes, "ShaderNodeMix", "Add Rim Light", (1300, 0))
    rim_add.data_type = 'RGBA'
    rim_add.blend_type = 'ADD'
    link(links, rim_step.outputs[0], find_socket(rim_add.inputs, "Factor", "Fac"))
    link(links, find_socket(apply_lineart.outputs, "Result", "Color"), find_socket(rim_add.inputs, "A", "Color1"))
    find_socket(rim_add.inputs, "B", "Color2").default_value = (0.9, 0.9, 1.0, 1.0)

    final_add = make_node(nodes, "ShaderNodeMix", "Add Final Emission", (1900, 0))
    final_add.data_type = 'RGBA'
    final_add.blend_type = 'ADD'
    find_socket(final_add.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(rim_add.outputs, "Result", "Color"), find_socket(final_add.inputs, "A", "Color1"))
    link(links, find_socket(shade_emission_final.outputs, "Result", "Color"), find_socket(final_add.inputs, "B", "Color2"))

    link(links, find_socket(final_add.outputs, "Result", "Color"), emission_out.inputs[0])

def build_baked_material(mat, base_img, emit_img, nmap_img, ilm_img, det_img, sdf_img=None):
    mat.use_nodes = True
    mat["is_anime_toon_baked"] = True 
    shader_type = mat.get("genos_shader_type", "DEFAULT")
    
    
    try:
        mat.blend_method = 'CLIP'
        mat.shadow_method = 'CLIP'
        mat.alpha_threshold = 0.5
        mat.show_transparent_back = False 
    except: pass

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = make_node(nodes, "ShaderNodeOutputMaterial", "Material Output", (3000, 0))
    mix_shader = make_node(nodes, "ShaderNodeMixShader", "Alpha Blend", (2700, 0))
    emission_out = make_node(nodes, "ShaderNodeEmission", "Anime Terminal Output", (2400, 0))
    transparent_bsdf = make_node(nodes, "ShaderNodeBsdfTransparent", "Transparency", (2400, -300))
    
    strength_val = make_node(nodes, "ShaderNodeValue", "Global Emission Strength", (2400, -150))
    strength_val.outputs[0].default_value = 2.0 
    
    link(links, strength_val.outputs[0], emission_out.inputs["Strength"])
    link(links, transparent_bsdf.outputs[0], mix_shader.inputs[1]) 
    link(links, emission_out.outputs[0], mix_shader.inputs[2])     
    link(links, mix_shader.outputs[0], output.inputs[0])

    base_tex = make_node(nodes, "ShaderNodeTexImage", "BaseColor", (-1500, 400))
    if base_img:
        set_image_colorspace(base_img, "sRGB")
        base_tex.image = base_img
    
    alpha_clip_gate = make_node(nodes, "ShaderNodeMath", "Alpha Failsafe", (2300, 200))
    alpha_clip_gate.operation = 'GREATER_THAN'
    alpha_clip_gate.inputs[1].default_value = 0.1
    link(links, base_tex.outputs[1], alpha_clip_gate.inputs[0])
    link(links, alpha_clip_gate.outputs[0], mix_shader.inputs[0])

    emission_map = make_node(nodes, "ShaderNodeTexImage", "Emission Map", (-1500, 300))
    if emit_img:
        set_image_colorspace(emit_img, "sRGB")
        emission_map.image = emit_img

    scene = bpy.context.scene if bpy.context else None
    emission_channel = getattr(scene, "genos_emission_channel", "RGBA")
    emission_source = emission_map
    if emission_channel != "RGBA":
        sep = make_node(nodes, "ShaderNodeSeparateColor", "Emission Channel Split", (-1300, 250))
        link(links, emission_map.outputs[0], sep.inputs[0])
        comb = make_node(nodes, "ShaderNodeCombineColor", "Emission Channel Combine", (-1000, 300))
        if emission_channel == "A":
            link(links, sep.outputs[3], comb.inputs[0])
            link(links, sep.outputs[3], comb.inputs[1])
            link(links, sep.outputs[3], comb.inputs[2])
        else:
            channel_map = {"R": 0, "G": 1, "B": 2}
            idx = channel_map.get(emission_channel, 0)
            link(links, sep.outputs[idx], comb.inputs[0])
            link(links, sep.outputs[idx], comb.inputs[1])
            link(links, sep.outputs[idx], comb.inputs[2])
        emission_source = comb

    ilm_tex = make_node(nodes, "ShaderNodeTexImage", "ILM MAP", (-1500, 100))
    if ilm_img:
        configure_mask_image(ilm_img, packed=True)
        ilm_tex.image = ilm_img
    ilm_sep = make_node(nodes, "ShaderNodeSeparateColor", "ILM Split", (-1200, 100))
    link(links, ilm_tex.outputs[0], ilm_sep.inputs[0])
    
    det_tex = make_node(nodes, "ShaderNodeTexImage", "Detail MAP", (-1500, -100))
    if det_img:
        configure_mask_image(det_img, packed=True)
        det_tex.image = det_img
    det_sep = make_node(nodes, "ShaderNodeSeparateColor", "Detail Split", (-1200, -100))
    link(links, det_tex.outputs[0], det_sep.inputs[0])

    normal_map_node = make_node(nodes, "ShaderNodeNormalMap", "Normal Map", (-1500, -800))
    if nmap_img:
        normal_tex = make_node(nodes, "ShaderNodeTexImage", "Normal_Tex", (-1800, -800))
        normal_tex.image = nmap_img
        try: normal_tex.image.colorspace_settings.name = 'Non-Color'
        except: pass
        link(links, normal_tex.outputs[0], normal_map_node.inputs["Color"])

    ao_mul = make_node(nodes, "ShaderNodeMix", "Apply Global AO", (-1200, 400))
    ao_mul.data_type = 'RGBA'
    ao_mul.blend_type = 'MULTIPLY'
    link(links, base_tex.outputs[0], find_socket(ao_mul.inputs, "A", "Color1"))
    link(links, det_sep.outputs[0], find_socket(ao_mul.inputs, "B", "Color2"))
    find_socket(ao_mul.inputs, "Factor", "Fac").default_value = 1.0

    accent_add = make_node(nodes, "ShaderNodeMix", "Detail Accent", (-900, 400))
    accent_add.data_type = 'RGBA'
    accent_add.blend_type = 'ADD'
    link(links, det_sep.outputs[2], find_socket(accent_add.inputs, "Factor", "Fac")) 
    link(links, find_socket(ao_mul.outputs, "Result", "Color"), find_socket(accent_add.inputs, "A", "Color1"))
    find_socket(accent_add.inputs, "B", "Color2").default_value = (1.0, 0.4, 0.4, 1.0) 

    diffuse = make_node(nodes, "ShaderNodeBsdfDiffuse", "Scene Light Capture", (-1200, -900))
    link(links, normal_map_node.outputs["Normal"], diffuse.inputs["Normal"])
    
    s2rgb = make_node(nodes, "ShaderNodeShaderToRGB", "Shader to RGB", (-900, -900))
    bw_light = make_node(nodes, "ShaderNodeRGBToBW", "Light Intensity", (-700, -900))
    link(links, diffuse.outputs[0], s2rgb.inputs[0])
    link(links, s2rgb.outputs[0], bw_light.inputs[0])

    shadow_offset = make_node(nodes, "ShaderNodeMath", "Normalize ILM.R", (-800, -1000))
    shadow_offset.operation = 'SUBTRACT'
    link(links, ilm_sep.outputs[0], shadow_offset.inputs[0]) 
    shadow_offset.inputs[1].default_value = 0.5

    shadow_add = make_node(nodes, "ShaderNodeMath", "Apply Shadow Bias", (-500, -900))
    shadow_add.operation = 'ADD'
    link(links, bw_light.outputs[0], shadow_add.inputs[0])
    link(links, shadow_offset.outputs[0], shadow_add.inputs[1]) 

    # SDF Logic for Baked Mat
    if shader_type == 'FACE':
        sdf_tex_node = make_node(nodes, "ShaderNodeTexImage", "SDF Map", (-700, -1100))
        if sdf_img:
            sdf_tex_node.image = sdf_img
            try: sdf_tex_node.image.colorspace_settings.name = 'Non-Color'
            except: pass
            
        sdf_min = make_node(nodes, "ShaderNodeMath", "SDF Min Edge", (-500, -1050))
        sdf_min.operation = 'SUBTRACT'
        link(links, sdf_tex_node.outputs[0], sdf_min.inputs[0])
        sdf_min.inputs[1].default_value = 0.05
        
        sdf_max = make_node(nodes, "ShaderNodeMath", "SDF Max Edge", (-500, -1150))
        sdf_max.operation = 'ADD'
        link(links, sdf_tex_node.outputs[0], sdf_max.inputs[0])
        sdf_max.inputs[1].default_value = 0.05
        
        shadow_step = make_node(nodes, "ShaderNodeMapRange", "SDF Shadow Edge", (-300, -900))
        shadow_step.interpolation_type = 'SMOOTHSTEP'
        link(links, shadow_add.outputs[0], shadow_step.inputs[0])
        link(links, sdf_min.outputs[0], shadow_step.inputs[1])
        link(links, sdf_max.outputs[0], shadow_step.inputs[2])
    else:
        shadow_step = make_node(nodes, "ShaderNodeMapRange", "Smooth Shadow Edge", (-300, -900))
        shadow_step.interpolation_type = 'SMOOTHSTEP'
        shadow_step.inputs[1].default_value = 0.45
        shadow_step.inputs[2].default_value = 0.55
        link(links, shadow_add.outputs[0], shadow_step.inputs[0])

    # Hair Specular for Baked Mat
    if shader_type == 'HAIR':
        glossy = make_node(nodes, "ShaderNodeBsdfAnisotropic", "Hair Specular", (-1200, -700))
        if glossy is None:
            glossy = make_node(nodes, "ShaderNodeBsdfGlossy", "Hair Specular", (-1200, -700))
            try:
                glossy.inputs["Roughness"].default_value = 0.2
                glossy.inputs["Anisotropy"].default_value = 0.8
                glossy.inputs["Rotation"].default_value = 0.25
            except: pass
        else:
            try:
                glossy.inputs["Roughness"].default_value = 0.2
                glossy.inputs["Anisotropy"].default_value = 0.8
                glossy.inputs["Rotation"].default_value = 0.25
            except: pass
    else:
        glossy = make_node(nodes, "ShaderNodeBsdfGlossy", "Specular Capture", (-1200, -700))
        try: glossy.inputs["Roughness"].default_value = 0.05
        except: pass
    
    find_socket(glossy.inputs, "Roughness").default_value = 0.05
    link(links, normal_map_node.outputs["Normal"], glossy.inputs["Normal"])
    
    gs2rgb = make_node(nodes, "ShaderNodeShaderToRGB", "Glossy to RGB", (-900, -700))
    gbw_light = make_node(nodes, "ShaderNodeRGBToBW", "Glossy Intensity", (-700, -700))
    link(links, glossy.outputs[0], gs2rgb.inputs[0])
    link(links, gs2rgb.outputs[0], gbw_light.inputs[0])

    spec_thresh = make_node(nodes, "ShaderNodeValue", "Specular Threshold", (-900, -600))
    spec_thresh.outputs[0].default_value = 0.6  
    
    spec_smooth = make_node(nodes, "ShaderNodeMath", "Spec Smooth Edge", (-700, -600))
    spec_smooth.operation = 'ADD'
    spec_smooth.inputs[1].default_value = 0.1
    link(links, spec_thresh.outputs[0], spec_smooth.inputs[0])

    glossy_step = make_node(nodes, "ShaderNodeMapRange", "Smooth Spec Edge", (-500, -700))
    glossy_step.interpolation_type = 'SMOOTHSTEP'
    link(links, spec_thresh.outputs[0], glossy_step.inputs["From Min"])
    link(links, spec_smooth.outputs[0], glossy_step.inputs["From Max"])
    link(links, gbw_light.outputs[0], glossy_step.inputs["Value"])

    glossy_mask = make_node(nodes, "ShaderNodeMath", "Mask Spec with ILM.B", (-300, -700))
    glossy_mask.operation = 'MULTIPLY'
    link(links, glossy_step.outputs[0], glossy_mask.inputs[0])
    link(links, ilm_sep.outputs[2], glossy_mask.inputs[1]) 

    shadow_tint = make_node(nodes, "ShaderNodeMix", "Shadow Color", (-200, 100))
    shadow_tint.data_type = 'RGBA'
    shadow_tint.blend_type = 'MULTIPLY'
    find_socket(shadow_tint.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(accent_add.outputs, "Result", "Color"), find_socket(shadow_tint.inputs, "A", "Color1"))
    find_socket(shadow_tint.inputs, "B", "Color2").default_value = (0.55, 0.55, 0.70, 1.0) 

    apply_shadow = make_node(nodes, "ShaderNodeMix", "Apply Shading", (300, 0))
    apply_shadow.data_type = 'RGBA'
    apply_shadow.blend_type = 'MIX'
    link(links, shadow_step.outputs[0], find_socket(apply_shadow.inputs, "Factor", "Fac"))
    link(links, find_socket(shadow_tint.outputs, "Result", "Color"), find_socket(apply_shadow.inputs, "A", "Color1")) 
    link(links, find_socket(accent_add.outputs, "Result", "Color"), find_socket(apply_shadow.inputs, "B", "Color2")) 

    spec_add = make_node(nodes, "ShaderNodeMix", "Add Dynamic Specular", (600, 0))
    spec_add.data_type = 'RGBA'
    spec_add.blend_type = 'ADD'
    link(links, glossy_mask.outputs[0], find_socket(spec_add.inputs, "Factor", "Fac")) 
    link(links, find_socket(apply_shadow.outputs, "Result", "Color"), find_socket(spec_add.inputs, "A", "Color1"))
    find_socket(spec_add.inputs, "B", "Color2").default_value = (1.0, 1.0, 1.0, 1.0)

    line_str = make_node(nodes, "ShaderNodeValue", "Inner Lineart Strength", (600, 200))
    line_str.outputs[0].default_value = 1.0
    
    line_mul = make_node(nodes, "ShaderNodeMath", "Scale Lineart", (800, 200))
    line_mul.operation = 'MULTIPLY'
    link(links, det_sep.outputs[1], line_mul.inputs[0]) 
    link(links, line_str.outputs[0], line_mul.inputs[1])

    line_inv = make_node(nodes, "ShaderNodeMath", "Invert Lineart", (1000, 200))
    line_inv.operation = 'SUBTRACT'
    line_inv.inputs[0].default_value = 1.0
    link(links, line_mul.outputs[0], line_inv.inputs[1])

    apply_lineart = make_node(nodes, "ShaderNodeMix", "Apply Lineart", (900, 0))
    apply_lineart.data_type = 'RGBA'
    apply_lineart.blend_type = 'MULTIPLY'
    find_socket(apply_lineart.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(spec_add.outputs, "Result", "Color"), find_socket(apply_lineart.inputs, "A", "Color1"))
    link(links, line_inv.outputs[0], find_socket(apply_lineart.inputs, "B", "Color2"))

    map_strength_val = make_node(nodes, "ShaderNodeValue", "Emission Map Strength", (1000, -350))
    map_strength_val.outputs[0].default_value = 10.0
    
    map_strength_mul = make_node(nodes, "ShaderNodeMix", "Scale Emission Map", (1200, -350))
    map_strength_mul.data_type = 'RGBA'
    map_strength_mul.blend_type = 'MULTIPLY'
    find_socket(map_strength_mul.inputs, "Factor", "Fac").default_value = 1.0
    link(links, emission_map.outputs[0], find_socket(map_strength_mul.inputs, "A", "Color1"))
    link(links, map_strength_val.outputs[0], find_socket(map_strength_mul.inputs, "B", "Color2"))

    combo_emit = make_node(nodes, "ShaderNodeMath", "Combine Glow Masks", (1000, -500))
    combo_emit.operation = 'ADD'
    link(links, ilm_sep.outputs[1], combo_emit.inputs[0]) 
    link(links, det_tex.outputs[1], combo_emit.inputs[1]) 

    emit_add_masks = make_node(nodes, "ShaderNodeMix", "Add Masked Base Glow", (1200, -500))
    emit_add_masks.data_type = 'RGBA'
    emit_add_masks.blend_type = 'ADD'
    link(links, combo_emit.outputs[0], find_socket(emit_add_masks.inputs, "Factor", "Fac"))
    find_socket(emit_add_masks.inputs, "A", "Color1").default_value = (0.0, 0.0, 0.0, 1.0)
    link(links, find_socket(accent_add.outputs, "Result", "Color"), find_socket(emit_add_masks.inputs, "B", "Color2"))

    total_emission = make_node(nodes, "ShaderNodeMix", "Total Raw Emission", (1400, -350))
    total_emission.data_type = 'RGBA'
    total_emission.blend_type = 'ADD'
    find_socket(total_emission.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(emit_add_masks.outputs, "Result", "Color"), find_socket(total_emission.inputs, "A", "Color1"))
    link(links, find_socket(map_strength_mul.outputs, "Result", "Color"), find_socket(total_emission.inputs, "B", "Color2"))

    emit_shadow_toggle = make_node(nodes, "ShaderNodeValue", "Emission Receives Shadows", (1300, -600))
    emit_shadow_toggle.outputs[0].default_value = 0.0 

    emit_shadow_mix = make_node(nodes, "ShaderNodeMix", "Shadowed Emission Blend", (1500, -600))
    emit_shadow_mix.data_type = 'FLOAT'
    link(links, emit_shadow_toggle.outputs[0], emit_shadow_mix.inputs[0])
    emit_shadow_mix.inputs[2].default_value = 1.0 
    link(links, shadow_step.outputs[0], emit_shadow_mix.inputs[3]) 

    emit_shadow_tint = make_node(nodes, "ShaderNodeMix", "Emissive Shadow Tint Color", (1500, -750))
    emit_shadow_tint.data_type = 'RGBA'
    emit_shadow_tint.blend_type = 'MULTIPLY'
    find_socket(emit_shadow_tint.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(total_emission.outputs, "Result", "Color"), find_socket(emit_shadow_tint.inputs, "A", "Color1"))
    find_socket(emit_shadow_tint.inputs, "B", "Color2").default_value = (0.2, 0.05, 0.5, 1.0)

    shade_emission_final = make_node(nodes, "ShaderNodeMix", "Apply Shadowed Emission", (1700, -350))
    shade_emission_final.data_type = 'RGBA'
    shade_emission_final.blend_type = 'MIX'
    link(links, emit_shadow_mix.outputs[0], find_socket(shade_emission_final.inputs, "Factor", "Fac")) 
    link(links, find_socket(emit_shadow_tint.outputs, "Result", "Color"), find_socket(shade_emission_final.inputs, "A", "Color1")) 
    link(links, find_socket(total_emission.outputs, "Result", "Color"), find_socket(shade_emission_final.inputs, "B", "Color2")) 

    fresnel = make_node(nodes, "ShaderNodeFresnel", "Rim Fresnel", (1500, -800))
    fresnel.inputs["IOR"].default_value = 1.1
    link(links, normal_map_node.outputs["Normal"], fresnel.inputs["Normal"])
    
    rim_mask = make_node(nodes, "ShaderNodeMath", "Mask Rim", (1700, -800))
    rim_mask.operation = 'MULTIPLY'
    link(links, fresnel.outputs[0], rim_mask.inputs[0])
    link(links, ilm_tex.outputs[1], rim_mask.inputs[1]) 
    
    rim_step = make_node(nodes, "ShaderNodeMapRange", "Smooth Rim Edge", (1900, -800))
    rim_step.interpolation_type = 'SMOOTHSTEP'
    rim_step.inputs[1].default_value = 0.45
    rim_step.inputs[2].default_value = 0.55
    link(links, rim_mask.outputs[0], rim_step.inputs[0])

    rim_add = make_node(nodes, "ShaderNodeMix", "Add Rim Light", (1300, 0))
    rim_add.data_type = 'RGBA'
    rim_add.blend_type = 'ADD'
    link(links, rim_step.outputs[0], find_socket(rim_add.inputs, "Factor", "Fac"))
    link(links, find_socket(apply_lineart.outputs, "Result", "Color"), find_socket(rim_add.inputs, "A", "Color1"))
    find_socket(rim_add.inputs, "B", "Color2").default_value = (0.9, 0.9, 1.0, 1.0)

    final_add = make_node(nodes, "ShaderNodeMix", "Add Final Emission", (1900, 0))
    final_add.data_type = 'RGBA'
    final_add.blend_type = 'ADD'
    find_socket(final_add.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(rim_add.outputs, "Result", "Color"), find_socket(final_add.inputs, "A", "Color1"))
    link(links, find_socket(shade_emission_final.outputs, "Result", "Color"), find_socket(final_add.inputs, "B", "Color2"))

    link(links, find_socket(final_add.outputs, "Result", "Color"), emission_out.inputs[0])

def current_paint_image(context):
    obj = active_mesh_object(context)
    if not obj or not obj.active_material: return None
    mat = obj.active_material
    target = context.scene.genos_paint_target
    
    node_map = {
        "BASECOLOR": "BaseColor",
        "EMISSION_MAP": "Emission Map",
        "ILM_SHADOW": "ILM_Shadow",
        "ILM_EMISSION": "ILM_Emission",
        "ILM_SPEC": "ILM_Spec",
        "ILM_RIM": "ILM_Rim",
        "DETAIL_AO": "Detail_AO",
        "DETAIL_CURVE": "Detail_Curve",
        "DETAIL_ACCENT": "Detail_Accent",
        "DETAIL_EMISSION": "Detail_Emission"
    }
    name = node_map.get(target)
    if name:
        node = mat.node_tree.nodes.get(name)
        return node.image if node else None
    return None

# -------------------------------------------------------------------
# Scene & Material Properties
# -------------------------------------------------------------------

def image_prop(name): return PointerProperty(name=name, type=bpy.types.Image)

def register_scene_props():
    bpy.types.Scene.genos_output_dir = StringProperty(name="Output Directory", subtype='DIR_PATH', default="//")
    bpy.types.Scene.genos_texture_size = IntProperty(name="Texture Size", default=DEFAULT_SIZE, min=256, max=8192)
    
    # FIXED: Added the Paint Toggle and the New Mesh Copy Toggle
    bpy.types.Scene.genos_autotoggle_paint = BoolProperty(name="Auto Switch to Texture Paint", default=False)
    bpy.types.Scene.genos_export_mesh_copy = BoolProperty(name="Create Baked Mesh Copy", default=False, description="Generates a copy of the mesh with the exported textures applied")
    
    bpy.types.Scene.genos_create_shader_type = EnumProperty(
        name="Shader Type",
        items=[
            ("DEFAULT", "Default Shader", "Standard Anime Shader"),
            ("FACE", "Face Shader (SDF)", "Uses an SDF map for precise shadow thresholds"),
            ("HAIR", "Hair Shader", "Uses Anisotropic highlighting"),
        ],
        default="DEFAULT"
    )

    bpy.types.Scene.genos_exp_suf_albedo = StringProperty(name="BaseColor Name", default="_BaseColor")
    bpy.types.Scene.genos_exp_suf_emission = StringProperty(name="Emission Name", default="_Emission")
    bpy.types.Scene.genos_exp_suf_ilm = StringProperty(name="ILM Name", default="_ILM")
    bpy.types.Scene.genos_exp_suf_detail = StringProperty(name="Detail Name", default="_Detail")
    bpy.types.Scene.genos_exp_suf_sdf = StringProperty(name="SDF Name", default="_SDF")

    bpy.types.Scene.genos_spec_mat_type = EnumProperty(
        name="Material Type",
        items=[
            ("HAIR", "Anime Hair (Halo)", ""),
            ("METAL", "Metal (Scattered)", ""),
            ("SKIN", "Skin (Soft Sheen)", ""),
            ("CLOTHES", "Clothes (Matte)", "")
        ],
        default="HAIR"
    )

    bpy.types.Scene.genos_paint_target = EnumProperty(
        name="Paint Target",
        items=[
            ("BASECOLOR", "BaseColor (Flat Albedo)", ""),
            ("EMISSION_MAP", "Emission Map (Colored Glow)", ""),
            ("ILM_SHADOW", "ILM.R (Shadow Offset)", ""),
            ("ILM_EMISSION", "ILM.G (Base Glow Mask)", ""),
            ("ILM_SPEC", "ILM.B (Specular Mask)", ""),
            ("ILM_RIM", "ILM.A (Rim Light Mask)", ""),
            ("DETAIL_AO", "Detail.R (Cavity AO)", ""),
            ("DETAIL_CURVE", "Detail.G (Curvature/Lines)", ""),
            ("DETAIL_ACCENT", "Detail.B (Decals/Blush)", ""),
            ("DETAIL_EMISSION", "Detail.A (Extra Glow)", ""),
        ],
        default="BASECOLOR"
    )
    
    bpy.types.Scene.genos_lineart_radius = FloatProperty(name="Edge Radius", default=0.03, min=0.001, max=0.5)

    bpy.types.Material.genos_normal_map = image_prop("Normal Map")
    bpy.types.Material.genos_ilm_packed = image_prop("ILM Packed")
    bpy.types.Material.genos_detail_packed = image_prop("Detail Packed")
    bpy.types.Material.genos_sdf_map = image_prop("SDF Map")

def unregister_scene_props():
    scene_props = [
        "genos_output_dir", "genos_texture_size", "genos_base_name", 
        "genos_autotoggle_paint", "genos_export_mesh_copy", # FIXED HERE
        "genos_paint_target", "genos_exp_suf_albedo", "genos_exp_suf_emission", "genos_exp_suf_ilm", 
        "genos_exp_suf_detail", "genos_exp_suf_sdf", "genos_spec_mat_type", "genos_lineart_radius", "genos_create_shader_type"
    ]
    for p in scene_props:
        if hasattr(bpy.types.Scene, p): delattr(bpy.types.Scene, p)
        
    mat_props = ["genos_normal_map", "genos_ilm_packed", "genos_detail_packed", "genos_sdf_map"]
    for p in mat_props:
        if hasattr(bpy.types.Material, p): delattr(bpy.types.Material, p)

# -------------------------------------------------------------------
# Operators
# -------------------------------------------------------------------

class GENOS_OT_fix_render_settings(bpy.types.Operator):
    bl_idname = "genos.fix_render_settings"
    bl_label = "Auto-Configure Eevee Next"

    def execute(self, context):
        scene = context.scene
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.look = 'None'
        for obj in scene.objects:
            if obj.type == 'LIGHT' and obj.data.type == 'SUN': obj.data.angle = 0.0  
        if hasattr(scene, "eevee"):
            try: scene.eevee.use_raytracing = False 
            except: pass
            try: scene.eevee.shadow_step_count = 2 
            except: pass
        self.report({'INFO'}, "Render settings optimized for Anime Shaders!")
        return {'FINISHED'}

class GENOS_OT_repair_textures(bpy.types.Operator):
    bl_idname = "genos.repair_textures"
    bl_label = "Repair Corrupted Masks"

    def execute(self, context):
        mat = context.active_object.active_material if context.active_object else None
        if not mat or "is_anime_toon" not in mat: return {'CANCELLED'}
        
        def reset_img(name, color, colorspace=MASK_COLORSPACE):
            node = mat.node_tree.nodes.get(name)
            if node and node.image:
                set_image_colorspace(node.image, colorspace)
                fill_image_solid(node.image, color)

        reset_img("BaseColor", (0.8, 0.8, 0.8, 1.0), "sRGB")
        reset_img("Emission Map", (0.0, 0.0, 0.0, 1.0), "sRGB")
        if mat.get("genos_shader_type") == 'FACE':
            reset_img("SDF Map", (0.5, 0.5, 0.5, 1.0), "Non-Color")
        reset_img("ILM_Shadow", (0.5, 0.5, 0.5, 1.0))
        reset_img("ILM_Emission", (0.0, 0.0, 0.0, 1.0))
        reset_img("ILM_Spec", (0.0, 0.0, 0.0, 1.0))
        reset_img("ILM_Rim", (0.0, 0.0, 0.0, 1.0))
        reset_img("Detail_AO", (1.0, 1.0, 1.0, 1.0))
        reset_img("Detail_Curve", (0.0, 0.0, 0.0, 1.0))
        reset_img("Detail_Accent", (0.0, 0.0, 0.0, 1.0))
        reset_img("Detail_Emission", (0.0, 0.0, 0.0, 1.0))
        
        self.report({'INFO'}, "Successfully restored all default texture data.")
        return {'FINISHED'}

class GENOS_OT_create_workspace(bpy.types.Operator):
    bl_idname = "genos.create_workspace"
    bl_label = "Create Shader Workspace"

    def execute(self, context):
        s = context.scene
        size = s.genos_texture_size

        obj = context.active_object
        if obj and obj.active_material: mat_base = obj.active_material.name
        else: mat_base = s.genos_base_name if hasattr(s, 'genos_base_name') else "Hero_Anime_Shader"

        mat_name = get_mat_name(mat_base)
        mat = bpy.data.materials.get(mat_name)
        if mat is None: mat = bpy.data.materials.new(mat_name)
        # store shader type on the material so other operators can read it
        try:
            mat["genos_shader_type"] = s.genos_create_shader_type
        except Exception:
            pass
        base_img = make_image(f"{mat_base}_BaseColor", size, size, alpha=True, colorspace="sRGB", color=(0.8, 0.8, 0.8, 1.0))
        emission_map = make_image(f"{mat_base}_EmissionMap", size, size, alpha=True, colorspace="sRGB", color=(0.0, 0.0, 0.0, 1.0))
        
        # PACKED MAPS: create (RGBA) textures and ensure alpha is 1.0 to avoid export invisibility
        mat.genos_ilm_packed = make_image(f"{mat_base}_ILM", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.5, 0.0, 0.0, 1.0))
        mat.genos_detail_packed = make_image(f"{mat_base}_Detail", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(1.0, 0.0, 0.0, 1.0))
        configure_mask_image(mat.genos_ilm_packed, packed=True)
        configure_mask_image(mat.genos_detail_packed, packed=True)

        images = {
            "basecolor": base_img,
            "emission_map": emission_map,
            "normal_map": mat.genos_normal_map,
            "ilm_shadow": make_image(f"{mat_base}_ILM_ShadowSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.5, 0.5, 0.5, 1.0)),
            "ilm_emission": make_image(f"{mat_base}_ILM_EmissionSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "ilm_spec": make_image(f"{mat_base}_ILM_SpecSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "ilm_rim": make_image(f"{mat_base}_ILM_RimSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "detail_ao": make_image(f"{mat_base}_Detail_AOSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(1.0, 1.0, 1.0, 1.0)),
            "detail_curve": make_image(f"{mat_base}_Detail_CurveSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "detail_accent": make_image(f"{mat_base}_Detail_AccentSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "detail_emission": make_image(f"{mat_base}_Detail_EmissionSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
        }


        if mat.get("genos_shader_type") == 'FACE':
            images["sdf_map"] = make_image(f"{mat_base}_SDFMap", size, size, alpha=False, colorspace="Non-Color", color=(0.5, 0.5, 0.5, 1.0))

        # If user already has exported packed ILM/Detail/SDF images, plug them in
        try:
            try_load_packed_maps_into_images(mat, images)
        except Exception:
            pass

        build_preview_material(mat, images)

        obj = active_mesh_object(context)
        if obj is not None:
            if not obj.material_slots: 
                bpy.ops.object.material_slot_add()
            obj.material_slots[0].link = 'OBJECT'
            obj.material_slots[0].material = mat

        self.report({'INFO'}, f"Created Workspace for '{mat_base}'.")
        return {'FINISHED'}

class GENOS_OT_regenerate_shader(bpy.types.Operator):
    bl_idname = "genos.regenerate_shader"
    bl_label = "Regenerate Node Tree"

    def execute(self, context):
        obj = context.active_object
        if not obj or not obj.active_material: return {'CANCELLED'}
        mat = obj.active_material

        if "is_anime_toon" not in mat:
            self.report({'ERROR'}, "Active material is not an AnimeToon shader.")
            return {'CANCELLED'}

        try:
            mat["genos_shader_type"] = context.scene.genos_create_shader_type
        except Exception:
            pass

        def get_node_img(node_name):
            node = mat.node_tree.nodes.get(node_name)
            return node.image if node else None

        images = {
            "basecolor": get_node_img("BaseColor"),
            "emission_map": get_node_img("Emission Map"),
            "normal_map": mat.genos_normal_map,
            "ilm_shadow": get_node_img("ILM_Shadow"),
            "ilm_emission": get_node_img("ILM_Emission"),
            "ilm_spec": get_node_img("ILM_Spec"),
            "ilm_rim": get_node_img("ILM_Rim"),
            "detail_ao": get_node_img("Detail_AO"),
            "detail_curve": get_node_img("Detail_Curve"),
            "detail_accent": get_node_img("Detail_Accent"),
            "detail_emission": get_node_img("Detail_Emission"),
            "sdf_map": get_node_img("SDF Map"),
        }
        if mat.get("genos_shader_type") == 'FACE':
            images["sdf_map"] = get_node_img("SDF Map")


        mask_keys = {
            "ilm_shadow", "ilm_emission", "ilm_spec", "ilm_rim",
            "detail_ao", "detail_curve", "detail_accent", "detail_emission"
        }
        # SDF map should be Non-Color when present
        mask_keys.add("sdf_map")
        for key, img in images.items():
            if img:
                if key == "normal_map" or key in mask_keys:
                    try: img.colorspace_settings.name = 'Non-Color'
                    except: pass
                else:
                    try: img.colorspace_settings.name = 'sRGB'
                    except: pass

        # Prefer any already-exported packed ILM/Detail maps when regenerating
        try:
            try_load_packed_maps_into_images(mat, images)
        except Exception:
            pass

        build_preview_material(mat, images)
        self.report({'INFO'}, f"Failsafe Cleaned & Regenerated Node Tree for {mat.name}")
        return {'FINISHED'}

class GENOS_OT_bake_specular(bpy.types.Operator):
    bl_idname = "genos.bake_specular"
    bl_label = "Auto-Bake Specular"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH': return {'CANCELLED'}
        # Preview appearance uses Eevee-style compositing and does not require a UV bake.

        mat = obj.active_material
        if not mat: return {'CANCELLED'}

        spec_node = mat.node_tree.nodes.get("ILM_Spec")
        if not spec_node or not spec_node.image: return {'CANCELLED'}

        orig_mode = obj.mode
        if orig_mode != 'OBJECT':
            try: bpy.ops.object.mode_set(mode='OBJECT')
            except Exception: pass

        temp_mat = bpy.data.materials.new("TEMP_BAKE")
        temp_mat.use_nodes = True
        temp_mat.node_tree.nodes.clear()

        out = temp_mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
        emit = temp_mat.node_tree.nodes.new("ShaderNodeEmission")

        mat_type = context.scene.genos_spec_mat_type

        if mat_type == 'HAIR':
            tex_coord = temp_mat.node_tree.nodes.new("ShaderNodeTexCoord")
            mapping = temp_mat.node_tree.nodes.new("ShaderNodeMapping")
            mapping.inputs["Scale"].default_value = (1.0, 1.0, 0.15)
            wave = temp_mat.node_tree.nodes.new("ShaderNodeTexWave")
            wave.wave_type = 'BANDS'
            wave.bands_direction = 'Z'
            ramp = temp_mat.node_tree.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = 0.45; ramp.color_ramp.elements[0].color = (0,0,0,1)
            ramp.color_ramp.elements[1].position = 0.55; ramp.color_ramp.elements[1].color = (1,1,1,1)
            fresnel = temp_mat.node_tree.nodes.new("ShaderNodeFresnel")
            fresnel.inputs["IOR"].default_value = 1.3
            mult = temp_mat.node_tree.nodes.new("ShaderNodeMath")
            mult.operation = 'MULTIPLY'
            temp_mat.node_tree.links.new(tex_coord.outputs["Generated"], mapping.inputs[0])
            temp_mat.node_tree.links.new(mapping.outputs[0], wave.inputs[0])
            temp_mat.node_tree.links.new(wave.outputs["Color"], ramp.inputs[0])
            temp_mat.node_tree.links.new(ramp.outputs[0], mult.inputs[0])
            temp_mat.node_tree.links.new(fresnel.outputs[0], mult.inputs[1])
            temp_mat.node_tree.links.new(mult.outputs[0], emit.inputs[0])
        elif mat_type == 'METAL':
            noise = temp_mat.node_tree.nodes.new("ShaderNodeTexNoise")
            noise.inputs["Scale"].default_value = 15.0
            ramp = temp_mat.node_tree.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = 0.6; ramp.color_ramp.elements[0].color = (0,0,0,1)
            ramp.color_ramp.elements[1].position = 0.65; ramp.color_ramp.elements[1].color = (1,1,1,1)
            temp_mat.node_tree.links.new(noise.outputs["Fac"], ramp.inputs[0])
            temp_mat.node_tree.links.new(ramp.outputs[0], emit.inputs[0])
        elif mat_type == 'SKIN':
            fresnel = temp_mat.node_tree.nodes.new("ShaderNodeFresnel")
            fresnel.inputs["IOR"].default_value = 1.05
            ramp = temp_mat.node_tree.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = 0.0; ramp.color_ramp.elements[0].color = (0,0,0,1)
            ramp.color_ramp.elements[1].position = 0.7; ramp.color_ramp.elements[1].color = (1,1,1,1)
            temp_mat.node_tree.links.new(fresnel.outputs[0], ramp.inputs[0])
            temp_mat.node_tree.links.new(ramp.outputs[0], emit.inputs[0])
        else:
            emit.inputs[0].default_value = (0,0,0,1)

        temp_mat.node_tree.links.new(emit.outputs[0], out.inputs[0])
        img_node = temp_mat.node_tree.nodes.new("ShaderNodeTexImage")
        img_node.name = "ILM_Spec"
        img_node.label = "ILM_Spec"
        img_node.image = spec_node.image

        orig_mats = [s.material for s in obj.material_slots]
        orig_active_index = obj.active_material_index
        success = False
        try:
            for s in obj.material_slots:
                s.material = temp_mat
            success = execute_bake(context, temp_mat, "ILM_Spec", is_ao=False)
        finally:
            for i, s in enumerate(obj.material_slots):
                if i < len(orig_mats):
                    s.material = orig_mats[i]
            obj.active_material_index = orig_active_index
            bpy.data.materials.remove(temp_mat)
            if orig_mode != 'OBJECT':
                try: bpy.ops.object.mode_set(mode=orig_mode)
                except Exception: pass

        if success:
            pack_material_ilm(mat)
            self.report({'INFO'}, "Baked ILM specular and repacked ILM texture.")
            return {'FINISHED'}

        self.report({'ERROR'}, "Specular bake failed. Check UVs and the active image target.")
        return {'CANCELLED'}

class GENOS_OT_bake_ao(bpy.types.Operator):
    bl_idname = "genos.bake_ao"
    bl_label = "Auto-Bake AO Map"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH': return {'CANCELLED'}
        # Preview appearance uses Eevee-style compositing and does not require a UV bake.
            
        mat = obj.active_material
        if not mat or not mat.use_nodes or "is_anime_toon" not in mat: return {'CANCELLED'}
            
        ao_node = mat.node_tree.nodes.get("Detail_AO")
        if not ao_node or not ao_node.image: return {'CANCELLED'}
        ao_img = ao_node.image
        configure_mask_image(ao_img)

        orig_mode = obj.mode
        if orig_mode != 'OBJECT':
            try: bpy.ops.object.mode_set(mode='OBJECT')
            except Exception: pass

        hidden_states = {}
        for o in context.scene.objects:
            hidden_states[o] = o.hide_render
            if o != obj: o.hide_render = True

        temp_ao_mat = bpy.data.materials.new("TEMP_BAKE_AO")
        temp_ao_mat.use_nodes = True
        tnodes = temp_ao_mat.node_tree.nodes
        tlinks = temp_ao_mat.node_tree.links
        tnodes.clear()
        
        out = tnodes.new("ShaderNodeOutputMaterial")
        bsdf = tnodes.new("ShaderNodeBsdfPrincipled")
        tlinks.new(bsdf.outputs[0], out.inputs[0])
        
        nmap = mat.genos_normal_map
        if nmap:
            set_image_colorspace(nmap, MASK_COLORSPACE)
            n_tex = tnodes.new("ShaderNodeTexImage")
            n_tex.image = nmap
            n_map_node = tnodes.new("ShaderNodeNormalMap")
            tlinks.new(n_tex.outputs[0], n_map_node.inputs["Color"])
            tlinks.new(n_map_node.outputs["Normal"], bsdf.inputs["Normal"])
        
        img_node = tnodes.new("ShaderNodeTexImage")
        img_node.name = "Detail_AO"
        img_node.label = "Detail_AO"
        img_node.image = ao_img
        tnodes.active = img_node
        img_node.select = True

        orig_mats = [slot.material for slot in obj.material_slots]
        orig_active_index = obj.active_material_index
        if not obj.material_slots:
            bpy.ops.object.material_slot_add()
            orig_mats = [None]
            
        for slot in obj.material_slots:
            slot.material = temp_ao_mat
        
        success = False
        try:
            success = execute_bake(context, temp_ao_mat, "Detail_AO", is_ao=True)
        except Exception as e: 
            self.report({'ERROR'}, f"AO Bake failed: {e}")
        finally:
            for i, slot in enumerate(obj.material_slots):
                if i < len(orig_mats): slot.material = orig_mats[i]
            obj.active_material_index = orig_active_index
            bpy.data.materials.remove(temp_ao_mat)
            
            for o, state in hidden_states.items():
                o.hide_render = state
            if orig_mode != 'OBJECT':
                try: bpy.ops.object.mode_set(mode=orig_mode)
                except Exception: pass
                 
        if success:
            pack_material_detail(mat)
            self.report({'INFO'}, "Successfully baked isolated AO and repacked Detail texture.")
            return {'FINISHED'}

        self.report({'ERROR'}, "AO bake failed. Check UVs and the active image target.")
        return {'CANCELLED'}

class GENOS_OT_bake_curvature(bpy.types.Operator):
    bl_idname = "genos.bake_curvature"
    bl_label = "Auto-Bake Lineart"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH': return {'CANCELLED'}
        # Preview appearance uses Eevee-style compositing and does not require a UV bake.

        mat = obj.active_material
        if not mat: return {'CANCELLED'}

        curve_node = mat.node_tree.nodes.get("Detail_Curve")
        if not curve_node or not curve_node.image: return {'CANCELLED'}

        orig_mode = obj.mode
        if orig_mode != 'OBJECT':
            try: bpy.ops.object.mode_set(mode='OBJECT')
            except Exception: pass

        temp_mat = bpy.data.materials.new("TEMP_BAKE")
        temp_mat.use_nodes = True
        temp_mat.node_tree.nodes.clear()

        out = temp_mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
        emit = temp_mat.node_tree.nodes.new("ShaderNodeEmission")
        geom = temp_mat.node_tree.nodes.new("ShaderNodeNewGeometry")
        bevel = temp_mat.node_tree.nodes.new("ShaderNodeBevel")
        bevel.inputs["Radius"].default_value = context.scene.genos_lineart_radius
        bevel.samples = 12

        dist = temp_mat.node_tree.nodes.new("ShaderNodeVectorMath")
        dist.operation = 'DISTANCE'
        temp_mat.node_tree.links.new(bevel.outputs["Normal"], dist.inputs[1])

        if mat.genos_normal_map:
            set_image_colorspace(mat.genos_normal_map, MASK_COLORSPACE)
            n_tex = temp_mat.node_tree.nodes.new("ShaderNodeTexImage")
            n_tex.image = mat.genos_normal_map
            n_map = temp_mat.node_tree.nodes.new("ShaderNodeNormalMap")
            temp_mat.node_tree.links.new(n_tex.outputs[0], n_map.inputs["Color"])
            temp_mat.node_tree.links.new(n_map.outputs["Normal"], dist.inputs[0])
            temp_mat.node_tree.links.new(n_map.outputs["Normal"], bevel.inputs["Normal"])
        else:
            temp_mat.node_tree.links.new(geom.outputs["Normal"], dist.inputs[0])

        ramp = temp_mat.node_tree.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].position = 0.01
        ramp.color_ramp.elements[0].color = (0,0,0,1)
        ramp.color_ramp.elements[1].position = 0.15
        ramp.color_ramp.elements[1].color = (1,1,1,1)

        temp_mat.node_tree.links.new(dist.outputs["Value"], ramp.inputs[0])
        temp_mat.node_tree.links.new(ramp.outputs[0], emit.inputs[0])
        temp_mat.node_tree.links.new(emit.outputs[0], out.inputs[0])

        img_node = temp_mat.node_tree.nodes.new("ShaderNodeTexImage")
        img_node.name = "Detail_Curve"
        img_node.label = "Detail_Curve"
        img_node.image = curve_node.image

        orig_mats = [s.material for s in obj.material_slots]
        orig_active_index = obj.active_material_index
        success = False
        try:
            for s in obj.material_slots:
                s.material = temp_mat
            success = execute_bake(context, temp_mat, "Detail_Curve", is_ao=False)
        finally:
            for i, s in enumerate(obj.material_slots):
                if i < len(orig_mats):
                    s.material = orig_mats[i]
            obj.active_material_index = orig_active_index
            bpy.data.materials.remove(temp_mat)
            if orig_mode != 'OBJECT':
                try: bpy.ops.object.mode_set(mode=orig_mode)
                except Exception: pass

        if success:
            pack_material_detail(mat)
            self.report({'INFO'}, "Baked Detail lineart and repacked Detail texture.")
            return {'FINISHED'}

        self.report({'ERROR'}, "Lineart bake failed. Check UVs and the active image target.")
        return {'CANCELLED'}

class GENOS_OT_set_paint_target(bpy.types.Operator):
    bl_idname = "genos.set_paint_target"
    bl_label = "Set Paint Target"

    def execute(self, context):
        obj = active_mesh_object(context)
        if not obj or not obj.active_material: return {'CANCELLED'}
        
        target = context.scene.genos_paint_target
        node = set_active_image_node(obj.active_material, target)
        
        if node is None or node.image is None: return {'CANCELLED'}
        
        # FIXED: Tell Blender's active tool system to target the node's image directly
        try:
            if getattr(context.tool_settings, "image_paint", None):
                context.tool_settings.image_paint.mode = 'IMAGE'
                context.tool_settings.image_paint.canvas = node.image
        except Exception as e:
            print(f"Paint Target Override Error: {e}")

        # Safely get the toggle property using getattr
        if getattr(context.scene, "genos_autotoggle_paint", False) and obj is not None:
            try: bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
            except Exception: pass
        return {'FINISHED'}
    
class GENOS_OT_bake_preview_appearance(bpy.types.Operator):
    bl_idname = "genos.bake_preview_appearance"
    bl_label = "Bake Preview Appearance"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        # Preview appearance uses Eevee-style compositing and does not require a UV bake.
        mat = obj.active_material
        if not mat or not mat.use_nodes or "is_anime_toon" not in mat:
            return {'CANCELLED'}

        img = bake_preview_texture(context, mat, emission_only=False)
        if img is None:
            self.report({'ERROR'}, "Preview appearance bake failed.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Baked preview appearance to {img.name}.")
        return {'FINISHED'}

class GENOS_OT_pack_ilm(bpy.types.Operator):
    bl_idname = "genos.pack_ilm"
    bl_label = "Pack Final ILM Texture"
    def execute(self, context):
        obj = context.active_object
        if not obj or not obj.active_material: return {'CANCELLED'}
        mat = obj.active_material
        if pack_material_ilm(mat) is None:
            self.report({'ERROR'}, "Could not pack ILM channels from the active material.")
            return {'CANCELLED'}
        self.report({'INFO'}, "Successfully Packed ILM Channels")
        return {'FINISHED'}

class GENOS_OT_pack_detail(bpy.types.Operator):
    bl_idname = "genos.pack_detail"
    bl_label = "Pack Final Detail Texture"
    def execute(self, context):
        obj = context.active_object
        if not obj or not obj.active_material: return {'CANCELLED'}
        mat = obj.active_material
        if pack_material_detail(mat) is None:
            self.report({'ERROR'}, "Could not pack Detail channels from the active material.")
            return {'CANCELLED'}
        self.report({'INFO'}, "Successfully Packed Detail Channels")
        return {'FINISHED'}

class GENOS_OT_save_all(bpy.types.Operator):
    bl_idname = "genos.save_all"
    bl_label = "Export Shader Textures"

    def execute(self, context):
        s = context.scene
        obj = context.active_object
        if not obj or not obj.active_material: return {'CANCELLED'}
        mat = obj.active_material
        
        mat_base = material_base_name(mat)
        
        out_dir = bpy.path.abspath(s.genos_output_dir)
        if not out_dir: 
            self.report({'ERROR'}, "Please set an Output Directory first!")
            return {'CANCELLED'}
        ensure_dir(out_dir)

        size = scene_texture_size()
        saved_count = 0

        def get_img(name):
            node = mat.node_tree.nodes.get(name)
            return node.image if node else None

        # -------------------------------------------------------------
        # 1. ILM Colored Packer (Solid RGB, NO transparency destruction)
        # -------------------------------------------------------------
        def create_ilm_colored_packer(mat_name, r_img, g_img, b_img):
            temp_mat = bpy.data.materials.new(mat_name)
            temp_mat.use_nodes = True
            nodes = temp_mat.node_tree.nodes
            links = temp_mat.node_tree.links
            nodes.clear()

            out = nodes.new("ShaderNodeOutputMaterial")
            emit = nodes.new("ShaderNodeEmission")
            links.new(emit.outputs[0], out.inputs[0])

            comb = nodes.new("ShaderNodeCombineColor")
            links.new(comb.outputs[0], emit.inputs["Color"])

            uv_node = nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = "Quad_UV"

            def add_tex(img, ch_idx, def_val):
                if not img:
                    comb.inputs[ch_idx].default_value = def_val
                    return
                tex = nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.interpolation = 'Linear'
                links.new(uv_node.outputs[0], tex.inputs["Vector"])
                
                # Extract pure Luminance/Red to prevent data mixing
                sep = nodes.new("ShaderNodeSeparateColor")
                links.new(tex.outputs["Color"], sep.inputs[0])
                links.new(sep.outputs[0], comb.inputs[ch_idx])

            # R=Shadow(0.5 bias), G=Emission(0.0), B=Spec(0.0)
            add_tex(r_img, 0, 0.5) 
            add_tex(g_img, 1, 0.0)
            add_tex(b_img, 2, 0.0)

            return temp_mat

        # -------------------------------------------------------------
        # 2. Detail B&W Packer (Pure Grayscale Multiplication)
        # -------------------------------------------------------------
        def create_detail_bw_packer(mat_name, ao_img, curve_img):
            temp_mat = bpy.data.materials.new(mat_name)
            temp_mat.use_nodes = True
            nodes = temp_mat.node_tree.nodes
            links = temp_mat.node_tree.links
            nodes.clear()

            out = nodes.new("ShaderNodeOutputMaterial")
            emit = nodes.new("ShaderNodeEmission")
            links.new(emit.outputs[0], out.inputs[0])

            uv_node = nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = "Quad_UV"

            # Math MULTIPLY node outputs pure Grayscale (Black & White)
            mul = nodes.new("ShaderNodeMath")
            mul.operation = 'MULTIPLY'
            links.new(mul.outputs[0], emit.inputs["Color"])

            def add_tex(img, slot_idx):
                if not img:
                    mul.inputs[slot_idx].default_value = 1.0
                    return
                tex = nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.interpolation = 'Linear'
                links.new(uv_node.outputs[0], tex.inputs["Vector"])
                
                sep = nodes.new("ShaderNodeSeparateColor")
                links.new(tex.outputs["Color"], sep.inputs[0])
                links.new(sep.outputs[0], mul.inputs[slot_idx])

            add_tex(ao_img, 0)
            add_tex(curve_img, 1)

            return temp_mat

        # -------------------------------------------------------------
        # 3. Standard Albedo/Emission Packer
        # -------------------------------------------------------------
        def create_simple_material(mat_name, img, has_alpha=False):
            temp_mat = bpy.data.materials.new(mat_name)
            temp_mat.use_nodes = True
            temp_mat.blend_method = 'BLEND'
            nodes = temp_mat.node_tree.nodes
            links = temp_mat.node_tree.links
            nodes.clear()
            
            out = nodes.new("ShaderNodeOutputMaterial")
            emit = nodes.new("ShaderNodeEmission")
            
            uv_node = nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = "Quad_UV"

            if img: 
                tex = nodes.new("ShaderNodeTexImage")
                tex.image = img
                links.new(uv_node.outputs[0], tex.inputs["Vector"])
                links.new(tex.outputs["Color"], emit.inputs["Color"])
                
                if has_alpha:
                    transp = nodes.new("ShaderNodeBsdfTransparent")
                    mix = nodes.new("ShaderNodeMixShader")
                    links.new(transp.outputs[0], mix.inputs[1])
                    links.new(emit.outputs[0], mix.inputs[2])
                    links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
                    links.new(mix.outputs[0], out.inputs[0])
                else:
                    links.new(emit.outputs[0], out.inputs[0])
                    
            return temp_mat

        packs_to_render = []

        # Queue 1: Colored ILM Map (Opaque RGB Format, use_alpha = False)
        ilm_mat = create_ilm_colored_packer(
            "TEMP_PACK_ILM",
            get_img("ILM_Shadow"),
            get_img("ILM_Emission"),
            get_img("ILM_Spec")
        )
        packs_to_render.append((ilm_mat, f"{mat_base}{getattr(s, 'genos_exp_suf_ilm', '_ILM')}.png", False))

        # Queue 2: Detail B&W Map (Opaque Grayscale Format, use_alpha = False)
        detail_mat = create_detail_bw_packer(
            "TEMP_PACK_DETAIL",
            get_img("Detail_AO"),
            get_img("Detail_Curve")
        )
        packs_to_render.append((detail_mat, f"{mat_base}{getattr(s, 'genos_exp_suf_detail', '_Detail')}.png", False))

        # Queue 3: Standard Maps (BaseColor supports Alpha, Emission is Opaque)
        base_img = get_img("BaseColor")
        if base_img:
            packs_to_render.append((create_simple_material("TEMP_PACK_BASE", base_img, True), f"{mat_base}{getattr(s, 'genos_exp_suf_albedo', '_BaseColor')}.png", True))
            
        emit_img = get_img("Emission Map")
        if emit_img:
            packs_to_render.append((create_simple_material("TEMP_PACK_EMIT", emit_img, False), f"{mat_base}{getattr(s, 'genos_exp_suf_emission', '_Emission')}.png", False))

        # Queue 4: Face SDF Map export when present
        if mat.get("genos_shader_type") == 'FACE':
            sdf_img = get_img("SDF Map")
            if sdf_img:
                packs_to_render.append((create_simple_material("TEMP_PACK_SDF", sdf_img, False), f"{mat_base}{getattr(s, 'genos_exp_suf_sdf', '_SDF')}.png", False))

        # Execute Live Camera Baking Queue
        for temp_mat, target_filename, use_alpha in packs_to_render:
            filepath = os.path.join(out_dir, target_filename)
            print(f"GENOS INFO: Camera-Baking map to: {target_filename}...")
            
            if _render_material_via_camera(context, temp_mat, size, filepath, use_alpha):
                saved_count += 1
            
            bpy.data.materials.remove(temp_mat)
                
        # --- FIXED: Only generate Baked Mesh copy if option is explicitly enabled ---
        if saved_count > 0 and getattr(s, 'genos_export_mesh_copy', False):
            baked_col = bpy.data.collections.get("Baked_Review")
            if not baked_col:
                baked_col = bpy.data.collections.new("Baked_Review")
                context.scene.collection.children.link(baked_col)

            new_obj = obj.copy()
            new_obj.data = obj.data.copy()
            new_obj.name = obj.name + "_Baked"
            baked_col.objects.link(new_obj)
            
            baked_mat = bpy.data.materials.get(mat.name + "_Baked")
            if not baked_mat:
                baked_mat = bpy.data.materials.new(mat.name + "_Baked")
            
            for i in range(len(new_obj.material_slots)):
                new_obj.material_slots[i].material = baked_mat
                
            def get_or_load(filepath, non_color=False):
                for i in bpy.data.images:
                    if i.filepath == filepath or i.filepath_raw == filepath:
                        i.reload()
                        if non_color:
                            try: i.colorspace_settings.name = 'Non-Color'
                            except: pass
                        return i
                try: 
                    i = bpy.data.images.load(filepath)
                    if non_color:
                        try: i.colorspace_settings.name = 'Non-Color'
                        except: pass
                    return i
                except: return None
            
            b_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_albedo', '_BaseColor')}.png")
            i_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_ilm', '_ILM')}.png")
            d_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_detail', '_Detail')}.png")
            e_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_emission', '_Emission')}.png")
            sdf_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_sdf', '_SDF')}.png")
            
            b_img = get_or_load(b_path)
            i_img = get_or_load(i_path, False)
            d_img = get_or_load(d_path, False)
            e_img = get_or_load(e_path)
            sdf_img = get_or_load(sdf_path, False) if mat.get("genos_shader_type") == 'FACE' else None
            
            try:
                build_baked_material(baked_mat, b_img, e_img, mat.genos_normal_map, i_img, d_img, sdf_img)
            except Exception as e:
                print(e)

        self.report({'INFO'}, f"Success! Exported {saved_count} maps for shader '{mat_base}'.")
        return {'FINISHED'}
    
class GENOS_OT_add_outline(bpy.types.Operator):
    bl_idname = "genos.add_outline"
    bl_label = "Add Anime Outline"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH': return {'CANCELLED'}

        mat_name = "AnimeToon_Outline"
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            mat = bpy.data.materials.new(mat_name)
            mat.use_nodes = True
            mat.use_backface_culling = True 
            
            nodes = mat.node_tree.nodes
            nodes.clear()
            out = nodes.new("ShaderNodeOutputMaterial")
            out.location = (300, 0)
            emit = nodes.new("ShaderNodeEmission")
            emit.location = (100, 0)
            emit.inputs[0].default_value = (0.0, 0.0, 0.0, 1.0) 
            mat.node_tree.links.new(emit.outputs[0], out.inputs[0])

        if mat.name not in [slot.name for slot in obj.material_slots]:
            obj.data.materials.append(mat)
            
        mat_idx = list(obj.data.materials).index(mat)

        mod = obj.modifiers.get("Anime Outline")
        if not mod: mod = obj.modifiers.new("Anime Outline", 'SOLIDIFY')
            
        mod.use_flip_normals = True
        mod.material_offset = mat_idx
        mod.thickness = -0.012 

        self.report({'INFO'}, "Anime Outline (Inverted Hull) applied!")
        return {'FINISHED'}

# -------------------------------------------------------------------
# UI - Professional Studio Layout
# -------------------------------------------------------------------

class GENOS_PT_workspace_panel(bpy.types.Panel):
    bl_label = "Anime Shader Studio"
    bl_idname = "GENOS_PT_workspace_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Anime Studio"

    def draw(self, context):
        layout = self.layout
        s = context.scene
        obj = context.active_object
        mat = obj.active_material if obj else None

        box = layout.box()
        box.label(text="Global Initialization", icon='SETTINGS')
        col = box.column(align=True)
        col.prop(s, "genos_base_name", text="New Shader Name")
        col.prop(s, "genos_texture_size")
        col.prop(s, "genos_output_dir")
        
        row = box.row(align=True)
        row.prop(s, "genos_create_shader_type", text="")
        row.operator("genos.create_workspace", icon='NODE_MATERIAL')
        
        row = box.row(align=True)
        row.operator("genos.fix_render_settings", icon='LIGHT_SUN', text="Fix Eevee Next")
        row.operator("genos.repair_textures", icon='RECOVER_LAST', text="Repair Textures")

        if mat and mat.use_nodes and "is_anime_toon" in mat:
            layout.separator()
            sh_type = mat.get("genos_shader_type", "DEFAULT")
            
            layout.label(text=f"Editing Shader: {mat.name}", icon='MATERIAL')

            img_box = layout.box()
            img_box.label(text="Assign Core Maps", icon='COLOR')
            img_box.prop(s, "genos_create_shader_type", text="Shader Type")
            img_box.prop(s, "genos_emission_channel", text="Emission Channel")
            
            base_node = mat.node_tree.nodes.get("BaseColor")
            if base_node: 
                img_box.label(text="Base Color (Albedo / Alpha):")
                img_box.template_ID(base_node, "image", open="image.open")
                
            emission_node = mat.node_tree.nodes.get("Emission Map")
            if emission_node: 
                img_box.label(text="Custom Emission:")
                img_box.template_ID(emission_node, "image", open="image.open")

            img_box.label(text="Normal Map (Bumps & Creases):")
            img_box.template_ID(mat, "genos_normal_map", open="image.open")

            img_box.label(text="Packed ILM Map (RGBA):")
            img_box.template_ID(mat, "genos_ilm_packed", open="image.open")

            img_box.label(text="Packed Detail Map (RGBA):")
            img_box.template_ID(mat, "genos_detail_packed", open="image.open")

            if sh_type == 'FACE':
                sdf_node = mat.node_tree.nodes.get("SDF Map")
                if sdf_node:
                    img_box.label(text="Face SDF Map:")
                    img_box.template_ID(mat, "genos_sdf_map", open="image.open")

            paint_box = layout.box()
            paint_box.label(text="LIVE Texture Painter", icon='BRUSH_DATA')
            paint_box.prop(s, "genos_paint_target", text="")
            row = paint_box.row(align=True)
            row.prop(s, "genos_autotoggle_paint", text="Auto-Switch Mode", icon='BRUSH_DATA')
            row.operator("genos.set_paint_target", icon='RESTRICT_SELECT_OFF', text="Start Painting")

            tools_box = layout.box()
            tools_box.label(text="Shader Tools & Bakers", icon='TOOL_SETTINGS')
            tools_box.operator("genos.add_outline", icon='MOD_SOLIDIFY', text="Add True Anime Outline")
            tools_box.operator("genos.regenerate_shader", icon='FILE_REFRESH', text="Clean/Regen Shader")
            tools_box.operator("genos.bake_preview_appearance", icon='RENDER_STILL', text="Bake Preview Appearance")
            
            row = tools_box.row(align=True)
            row.operator("genos.bake_ao", icon='SHADING_RENDERED', text="Bake AO")
            
            curve_box = tools_box.box()
            curve_box.label(text="Auto-Generate Edge Lineart:")
            row = curve_box.row(align=True)
            row.prop(s, "genos_lineart_radius", text="Radius")
            row.operator("genos.bake_curvature", icon='MATCLOTH', text="Bake Lineart")

            spec_box = tools_box.box()
            spec_box.label(text="Auto-Generate Specular:")
            row = spec_box.row(align=True)
            row.prop(s, "genos_spec_mat_type", text="")
            row.operator("genos.bake_specular", icon='LIGHT_SUN', text="Bake Specular")
            
            live_box = layout.box()
            live_box.label(text="Live Texture Packing", icon='TEXTURE')
            live_row = live_box.row(align=True)
            live_row.operator("genos.pack_ilm", icon='MOD_HUE_SATURATION', text="Pack ILM Maps")
            live_row.operator("genos.pack_detail", icon='MOD_HUE_SATURATION', text="Pack Detail Maps")

            pack_box = layout.box()
            pack_box.label(text="Final Game Export", icon='EXPORT')
            pack_box.label(text="Dynamically exports based on current Material Name", icon='INFO')
            
            name_box = pack_box.box()
            name_box.label(text="Target Output Suffixes:", icon='FILE_BLANK')
            name_col = name_box.column(align=True)
            name_col.prop(s, "genos_exp_suf_albedo", text="BaseColor")
            name_col.prop(s, "genos_exp_suf_emission", text="Emission")
            name_col.prop(s, "genos_exp_suf_ilm", text="ILM Name")
            name_col.prop(s, "genos_exp_suf_detail", text="Detail Name")
            if sh_type == 'FACE':
                name_col.prop(s, "genos_exp_suf_sdf", text="SDF Name")
            
            pack_box = layout.box()
            pack_box.label(text="Final Game Export", icon='EXPORT')
            
            # FIXED: Add the Mesh Export UI Toggle right here
            pack_box.prop(s, "genos_export_mesh_copy", text="Create Baked Mesh Copy")
            
            pack_box.label(text="Dynamically exports based on current Material Name", icon='INFO')
            
            name_box = pack_box.box()
            name_box.label(text="Target Output Suffixes:", icon='FILE_BLANK')
            name_col = name_box.column(align=True)
            name_col.prop(s, "genos_exp_suf_albedo", text="BaseColor")
            name_col.prop(s, "genos_exp_suf_emission", text="Emission")
            name_col.prop(s, "genos_exp_suf_ilm", text="ILM Name")
            name_col.prop(s, "genos_exp_suf_detail", text="Detail Name")
            if sh_type == 'FACE':
                name_col.prop(s, "genos_exp_suf_sdf", text="SDF Name")
            
            pack_box.operator("genos.save_all", icon='FILE_TICK', text=f"Export {mat.name} Maps")
        else:
            layout.separator()
            layout.label(text="Select an AnimeToon object", icon='INFO')
            layout.label(text="to edit its textures.", icon='BLANK1')

class GENOS_PT_guide_panel(bpy.types.Panel):
    bl_label = "Workflow Guide"
    bl_idname = "GENOS_PT_guide_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Anime Studio"
    bl_parent_id = "GENOS_PT_workspace_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        
        box = layout.box()
        box.label(text="1. Core Maps", icon='COLOR')
        box.label(text="• BaseColor: Paint FLAT colors. Use Erase Alpha for transparency.", icon='DOT')
        box.label(text="• Emission Map: Paint true glowing colored lights.", icon='DOT')
        box.label(text="• Normal Map: Load bumps to affect lighting & Lineart baking.", icon='DOT')

        box = layout.box()
        box.label(text="2. ILM Masks (Lighting)", icon='LIGHT')
        box.label(text="• ILM.R (Shadows): Black forces shadow, White forces light.", icon='DOT')
        box.label(text="• ILM.G (Glow Mask): White makes BaseColor glow.", icon='DOT')
        box.label(text="• ILM.B (Specular): Use Auto-Bake or Paint White for highlights.", icon='DOT')
        box.label(text="• ILM.A (Rim Light): White enables rim reflections.", icon='DOT')

        box = layout.box()
        box.label(text="3. Detail Masks", icon='BRUSH_DATA')
        box.label(text="• Detail.R (AO): Auto-baked soft occlusions.", icon='DOT')
        box.label(text="• Detail.G (Curvature): Auto-baked sharp inner lineart.", icon='DOT')
        box.label(text="• Detail.B (Accent): Overlays blush or body decals.", icon='DOT')
        box.label(text="• Detail.A (Extra): Secondary glowing layer.", icon='DOT')

# -------------------------------------------------------------------
# Registration
# -------------------------------------------------------------------

classes = (
    GENOS_OT_create_workspace, GENOS_OT_fix_render_settings, GENOS_OT_repair_textures, GENOS_OT_regenerate_shader, GENOS_OT_add_outline, 
    GENOS_OT_bake_ao, GENOS_OT_bake_curvature, GENOS_OT_bake_specular,
    GENOS_OT_set_paint_target, GENOS_OT_bake_preview_appearance,
    GENOS_OT_pack_ilm, GENOS_OT_pack_detail, GENOS_OT_save_all, 
    GENOS_PT_workspace_panel, GENOS_PT_guide_panel
)

def register():
    register_scene_props()
    for cls in classes: bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes): bpy.utils.unregister_class(cls)
    unregister_scene_props()

if __name__ == "__main__":
    register()