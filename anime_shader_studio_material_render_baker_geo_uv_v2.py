bl_info = {
    "name": "Anime Shader Studio (Blender 5.2)",
    "author": "AP",
    "version": (18, 11, 0),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > Anime Studio",
    "description": "Anime shader + full texture-map pipeline: dynamic hair bands, ILM/Detail, normal, displacement and PBR data baking/export.",
    "category": "Render",
}

import bpy
from bpy.app.handlers import persistent
try:
    import bmesh
except Exception:
    bmesh = None
import os
import tempfile
import shutil
import zipfile
import urllib.request
import urllib.error
import ssl

try:
    import numpy as np
except Exception:
    np = None

from bpy.props import StringProperty, IntProperty, BoolProperty, EnumProperty, PointerProperty, FloatProperty, FloatVectorProperty

# -------------------------------------------------------------------
# Configuration & Helpers
# -------------------------------------------------------------------

DEFAULT_SIZE = 1024
MASK_COLORSPACE = "Non-Color"
PACKED_ALPHA_MODE = "CHANNEL_PACKED"

PATTERN_PRESET_KEYS = ("PANTYHOSE", "STRIPES", "RIPPED", "BODYSUIT_HEX", "DOTS", "COTTON", "LEATHER")

def get_mat_name(base_name):
    return f"{base_name}_AnimeToon"

def ensure_dir(path: str):
    if path: os.makedirs(path, exist_ok=True)

def pattern_cache_root(scene=None):
    custom_dir = None
    try:
        custom_dir = getattr(scene or bpy.context.scene, "genos_pattern_cache_dir", "")
    except Exception:
        custom_dir = ""
    if custom_dir:
        try:
            root = bpy.path.abspath(custom_dir)
            ensure_dir(root)
            return root
        except Exception:
            pass
    root = bpy.utils.user_resource('SCRIPTS', path=os.path.join("addons_data", "anime_shader_studio", "patterns"), create=True)
    ensure_dir(root)
    return root

def pattern_preset_url(scene, key):
    key_map = {
        "PANTYHOSE": getattr(scene, "genos_pattern_url_pantyhose", ""),
        "STRIPES": getattr(scene, "genos_pattern_url_stripes", ""),
        "RIPPED": getattr(scene, "genos_pattern_url_ripped", ""),
        "BODYSUIT_HEX": getattr(scene, "genos_pattern_url_bodysuit", ""),
        "DOTS": getattr(scene, "genos_pattern_url_dots", ""),
        "COTTON": getattr(scene, "genos_pattern_url_cotton", ""),
        "LEATHER": getattr(scene, "genos_pattern_url_leather", ""),
    }
    return key_map.get(key, "")

def find_first_file(path, include_tokens):
    if not os.path.isdir(path):
        return None
    include_tokens = [t.lower() for t in include_tokens]
    for root, _, files in os.walk(path):
        for fname in files:
            low = fname.lower()
            if any(tok in low for tok in include_tokens):
                return os.path.join(root, fname)
    return None

def copy_if_exists(src, dst):
    if not src or not os.path.exists(src):
        return None
    shutil.copy2(src, dst)
    return dst

def copy_with_src_ext(src, dst_no_ext):
    if not src or not os.path.exists(src):
        return None
    ext = os.path.splitext(src)[1] or ".png"
    dst = dst_no_ext + ext
    shutil.copy2(src, dst)
    return dst

def _download_file(url, dst_path):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    })

    last_err = None
    contexts = [None]
    try:
        contexts.append(ssl._create_unverified_context())
    except Exception:
        pass

    for ctx in contexts:
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as response:
                with open(dst_path, 'wb') as f:
                    shutil.copyfileobj(response, f)
            if os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
                return
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Download failed for {url}: {last_err}")

def download_pattern_preset(scene, key):
    url = pattern_preset_url(scene, key)
    if not url:
        raise RuntimeError(f"No URL configured for preset: {key}")

    root = pattern_cache_root(scene)
    preset_dir = os.path.join(root, key.lower())
    ensure_dir(preset_dir)

    zip_path = os.path.join(preset_dir, "source.zip")
    _download_file(url, zip_path)

    if not zipfile.is_zipfile(zip_path):
        head = b""
        try:
            with open(zip_path, 'rb') as f:
                head = f.read(180)
        except Exception:
            pass
        head_txt = ""
        try:
            head_txt = head.decode('utf-8', errors='ignore').strip().replace('\n', ' ')[:120]
        except Exception:
            pass
        raise RuntimeError(f"Downloaded file is not a ZIP. URL may be blocked or redirected. Header preview: {head_txt}")

    extract_dir = os.path.join(preset_dir, "extracted")
    if os.path.isdir(extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
    ensure_dir(extract_dir)

    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(extract_dir)

    color_src = find_first_file(extract_dir, ["_color.", "color.", "albedo", "basecolor"])
    rough_src = find_first_file(extract_dir, ["_roughness.", "roughness."])
    normal_src = find_first_file(extract_dir, ["_normalgl.", "_normal.", "normal."])

    color_dst = copy_with_src_ext(color_src, os.path.join(preset_dir, "color"))
    rough_dst = copy_with_src_ext(rough_src, os.path.join(preset_dir, "roughness"))
    normal_dst = copy_with_src_ext(normal_src, os.path.join(preset_dir, "normal"))

    if not color_dst:
        raise RuntimeError("Texture pack downloaded, but no color/albedo/basecolor map was found inside ZIP.")

    return {
        "dir": preset_dir,
        "zip": zip_path,
        "color": color_dst,
        "roughness": rough_dst,
        "normal": normal_dst,
    }

def cached_pattern_paths(scene, key):
    preset_dir = os.path.join(pattern_cache_root(scene), key.lower())
    color = find_first_file(preset_dir, ["color.", "_color.", "albedo", "basecolor"])
    rough = find_first_file(preset_dir, ["roughness.", "_roughness."])
    normal = find_first_file(preset_dir, ["normal.", "_normal.", "_normalgl."])
    out = {
        "dir": preset_dir,
        "color": color,
        "roughness": rough,
        "normal": normal,
    }
    return out

def load_or_reload_image(path, *, non_color=False):
    if not path or not os.path.exists(path):
        return None
    abspath = os.path.abspath(path)
    for img in bpy.data.images:
        try:
            if bpy.path.abspath(img.filepath) == abspath or bpy.path.abspath(img.filepath_raw) == abspath:
                try:
                    img.reload()
                except Exception:
                    pass
                set_image_colorspace(img, 'Non-Color' if non_color else 'sRGB')
                return img
        except Exception:
            pass
    try:
        img = bpy.data.images.load(abspath)
        set_image_colorspace(img, 'Non-Color' if non_color else 'sRGB')
        return img
    except Exception:
        return None

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
        set_image_colorspace(img, 'Non-Color' if non_color else 'sRGB')
        return img
    except Exception:
        return None

def try_load_packed_maps_into_images(mat, images, only_missing=True):
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
            if stored and is_valid_image(stored):
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
            if not only_missing or not images.get(k):
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
            if not only_missing or not images.get(k):
                images[k] = det_img
    # SDF map candidates (face shader)
    sdf_candidates = {
        'prop': 'genos_sdf_map',
        'files': [f"{base}{getattr(scene, 'genos_exp_suf_sdf', '_SDF')}.png", f"{base}_SDF.png"],
        'names': [f"{base}_SDF", f"{base}{getattr(scene, 'genos_exp_suf_sdf', '_SDF')}.png"]
    }
    sdf_img = find_packed(sdf_candidates, non_color=True)
    if sdf_img and (not only_missing or not images.get("sdf_map")):
        images["sdf_map"] = sdf_img

def is_valid_image(img):
    """Safely determines if an image datablock contains valid dimensions or is backed by an external file / pack."""
    if img is None:
        return False
    try:
        if img.name not in bpy.data.images:
            return False
        if img.size[0] > 0 and img.size[1] > 0:
            return True
        if getattr(img, 'source', '') == 'FILE' or bool(getattr(img, 'filepath', '')) or bool(getattr(img, 'filepath_raw', '')):
            return True
        if getattr(img, 'packed_file', None) is not None:
            return True
        if getattr(img, 'has_data', False):
            return True
    except Exception:
        return False
    return False

def extract_source_textures_from_material(source_mat):
    """Extract common PBR/data textures without changing the source material.

    Recognized maps: BaseColor, Normal, Emission, Roughness, Metallic,
    Opacity/Alpha, AO and Displacement/Height. Data maps are always treated as
    Non-Color later in the pipeline.
    """
    tex_dict = {}
    if not source_mat or not getattr(source_mat, "use_nodes", False) or not getattr(source_mat, "node_tree", None):
        return tex_dict

    nodes = source_mat.node_tree.nodes

    def image_from_socket(sock):
        if not sock or not getattr(sock, 'links', None):
            return None
        if not sock.links:
            return None
        node = sock.links[0].from_node
        # Common wrappers around image textures.
        visited = set()
        while node and node not in visited:
            visited.add(node)
            if node.type == 'TEX_IMAGE' and is_valid_image(getattr(node, 'image', None)):
                return node.image
            next_node = None
            for inp in getattr(node, 'inputs', []):
                if getattr(inp, 'links', None) and inp.links:
                    cand = inp.links[0].from_node
                    if cand and cand not in visited:
                        next_node = cand
                        break
            node = next_node
        return None

    for node in nodes:
        if node.type not in {'BSDF_PRINCIPLED', 'BSDF_DIFFUSE'}:
            continue
        socket_map = {
            'basecolor': ('Base Color', 'Color'),
            'roughness_map': ('Roughness',),
            'metallic_map': ('Metallic',),
            'opacity_map': ('Alpha',),
            'emission_map': ('Emission Color', 'Emission'),
        }
        for key, names in socket_map.items():
            for name in names:
                sock = node.inputs.get(name)
                img = image_from_socket(sock)
                if img:
                    tex_dict[key] = img
                    break
            if key in tex_dict:
                continue

        norm_socket = node.inputs.get('Normal')
        if norm_socket and norm_socket.links:
            nnode = norm_socket.links[0].from_node
            if nnode.type == 'NORMAL_MAP':
                img = image_from_socket(nnode.inputs.get('Color'))
            else:
                img = image_from_socket(norm_socket)
            if img:
                tex_dict['normal_map'] = img

    # Material Output displacement or a Bump Height input can carry a height map.
    for out in [n for n in nodes if n.type == 'OUTPUT_MATERIAL']:
        img = image_from_socket(out.inputs.get('Displacement'))
        if img:
            tex_dict['displacement_map'] = img
            break
    if 'displacement_map' not in tex_dict:
        for node in nodes:
            if node.type == 'BUMP':
                img = image_from_socket(node.inputs.get('Height'))
                if img:
                    tex_dict['displacement_map'] = img
                    break

    # Name-based fallback covers imported engines and packed/custom node layouts.
    for node in nodes:
        if node.type != 'TEX_IMAGE' or not is_valid_image(getattr(node, 'image', None)):
            continue
        low = (node.name + ' ' + (node.label or '') + ' ' + node.image.name).lower()
        tests = (
            ('normal_map', ('normal', 'nrm')),
            ('roughness_map', ('rough', 'rgh')),
            ('metallic_map', ('metallic', 'metalness', 'metal')),
            ('opacity_map', ('opacity', 'alpha', 'transparency')),
            ('ao_map', ('ambientocclusion', 'ambient_occlusion', 'occlusion', '_ao', ' ao')),
            ('displacement_map', ('displacement', 'height', 'disp')),
            ('emission_map', ('emission', 'emit', 'glow')),
        )
        for key, tokens in tests:
            if key not in tex_dict and any(t in low for t in tokens):
                tex_dict[key] = node.image
        if 'basecolor' not in tex_dict:
            if any(t in low for t in ('basecolor', 'base_color', 'albedo', 'diffuse', 'diff')) and not any(t in low for t in ('normal','rough','metal','emit','alpha','opacity','ao','height','disp')):
                tex_dict['basecolor'] = node.image

    return tex_dict

def set_image_colorspace(img, colorspace):
    if img is None: return
    try:
        cs = getattr(img, 'colorspace_settings', None)
        if cs and cs.name != colorspace:
            # In Blender, setting colorspace_settings.name on an in-memory image
            # frees the buffer and wipes pixels to 0.0. Preserve pixels if present.
            has_px = getattr(img, 'has_data', False)
            px = None
            if has_px and img.size[0] > 0 and img.size[1] > 0:
                try:
                    if np is not None:
                        px = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
                        img.pixels.foreach_get(px)
                    else:
                        px = list(img.pixels)
                except Exception:
                    px = None
            cs.name = colorspace
            if px is not None and len(px) > 0:
                try:
                    img.pixels.foreach_set(px)
                    img.update()
                except Exception:
                    pass
    except Exception:
        pass

def set_channel_packed_alpha(img):
    if img is None: return
    try:
        if getattr(img, 'alpha_mode', None) != PACKED_ALPHA_MODE:
            img.alpha_mode = PACKED_ALPHA_MODE
    except Exception: pass

def ensure_image_data(img, fallback_color=None, width=DEFAULT_SIZE, height=DEFAULT_SIZE):
    if img is None:
        return None
    try:
        if is_valid_image(img):
            return img
    except Exception:
        pass

    # Preserve external/file-backed textures by reloading them instead of replacing with generated black data.
    try:
        if getattr(img, 'source', '') == 'FILE' or bool(getattr(img, 'filepath', '')) or bool(getattr(img, 'filepath_raw', '')):
            try:
                img.reload()
            except Exception:
                pass
            if is_valid_image(img):
                return img
    except Exception:
        pass

    if fallback_color is not None:
        fill_image_solid(img, fallback_color, width, height)
    return img

def configure_mask_image(img, *, packed=False):
    set_image_colorspace(img, MASK_COLORSPACE)
    if packed:
        set_channel_packed_alpha(img)

def fill_image_solid(img, color, w=DEFAULT_SIZE, h=DEFAULT_SIZE):
    if img is None: return
    
    if not is_valid_image(img):
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
    else:
        if not is_valid_image(img):
            ensure_image_data(img, color, width, height)
        
    set_image_colorspace(img, colorspace)
    return img

def get_image_pixels(img):
    if img is None or not is_valid_image(img):
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

def rasterize_uv_faces_to_image(obj, img, fill_color=(1.0, 1.0, 1.0, 1.0), target_key="ILM_EMISSION", blend_mode='REPLACE'):
    """
    Rasterizes the UV polygons of selected faces (or all faces if none selected in object mode)
    directly onto the specified Blender Image.
    Works seamlessly in EDIT mode or OBJECT mode.
    """
    if not obj or obj.type != 'MESH' or not img:
        return 0

    orig_mode = obj.mode
    is_edit = (orig_mode == 'EDIT')

    if is_edit:
        bm = bmesh.from_edit_mesh(obj.data)
    else:
        bm = bmesh.new()
        bm.from_mesh(obj.data)

    uv_layer = bm.loops.layers.uv.active
    if not uv_layer:
        uv_layer = bm.loops.layers.uv.verify()

    selected_faces = [f for f in bm.faces if f.select]
    if not selected_faces:
        if not is_edit:
            selected_faces = list(bm.faces)
        else:
            return 0

    w, h = img.size
    if w <= 0 or h <= 0:
        if not is_edit:
            bm.free()
        return 0

    raw_pixels = list(img.pixels[:])
    if len(raw_pixels) != w * h * 4:
        if not is_edit:
            bm.free()
        return 0

    if isinstance(fill_color, (int, float)):
        fill_rgba = (float(fill_color), float(fill_color), float(fill_color), 1.0)
    else:
        fill_rgba = tuple(float(c) for c in fill_color)
        if len(fill_rgba) < 4:
            fill_rgba = fill_rgba + (1.0,) * (4 - len(fill_rgba))

    if np is not None:
        pixel_arr = np.array(raw_pixels, dtype=np.float32).reshape((h, w, 4))
        for f in selected_faces:
            n_loops = len(f.loops)
            if n_loops < 3:
                continue
            for i in range(1, n_loops - 1):
                u0, v0 = f.loops[0][uv_layer].uv
                u1, v1 = f.loops[i][uv_layer].uv
                u2, v2 = f.loops[i+1][uv_layer].uv

                x0, y0 = u0 * (w - 1), v0 * (h - 1)
                x1, y1 = u1 * (w - 1), v1 * (h - 1)
                x2, y2 = u2 * (w - 1), v2 * (h - 1)

                xmin = max(0, min(w - 1, int(np.floor(min(x0, x1, x2)))))
                xmax = max(0, min(w - 1, int(np.ceil(max(x0, x1, x2)))))
                ymin = max(0, min(h - 1, int(np.floor(min(y0, y1, y2)))))
                ymax = max(0, min(h - 1, int(np.ceil(max(y0, y1, y2)))))

                if xmax < xmin or ymax < ymin:
                    continue

                xs = np.arange(xmin, xmax + 1, dtype=np.float32)
                ys = np.arange(ymin, ymax + 1, dtype=np.float32)
                grid_x, grid_y = np.meshgrid(xs, ys)

                e01 = (grid_x - x0) * (y1 - y0) - (grid_y - y0) * (x1 - x0)
                e12 = (grid_x - x1) * (y2 - y1) - (grid_y - y1) * (x2 - x1)
                e20 = (grid_x - x2) * (y0 - y2) - (grid_y - y2) * (x0 - x2)

                mask = ((e01 >= 0) & (e12 >= 0) & (e20 >= 0)) | ((e01 <= 0) & (e12 <= 0) & (e20 <= 0))

                sub_patch = pixel_arr[ymin:ymax+1, xmin:xmax+1]
                if blend_mode == 'ADD':
                    sub_patch[mask] = np.clip(sub_patch[mask] + np.array(fill_rgba, dtype=np.float32), 0.0, 1.0)
                elif blend_mode == 'MULTIPLY':
                    sub_patch[mask] = sub_patch[mask] * np.array(fill_rgba, dtype=np.float32)
                else:
                    sub_patch[mask] = np.array(fill_rgba, dtype=np.float32)

        try:
            img.pixels.foreach_set(pixel_arr.ravel())
        except Exception:
            img.pixels[:] = pixel_arr.ravel().tolist()
    else:
        for f in selected_faces:
            n_loops = len(f.loops)
            if n_loops < 3:
                continue
            for i in range(1, n_loops - 1):
                u0, v0 = f.loops[0][uv_layer].uv
                u1, v1 = f.loops[i][uv_layer].uv
                u2, v2 = f.loops[i+1][uv_layer].uv

                x0, y0 = int(u0 * (w - 1)), int(v0 * (h - 1))
                x1, y1 = int(u1 * (w - 1)), int(v1 * (h - 1))
                x2, y2 = int(u2 * (w - 1)), int(v2 * (h - 1))

                xmin = max(0, min(w - 1, min(x0, x1, x2)))
                xmax = max(0, min(w - 1, max(x0, x1, x2)))
                ymin = max(0, min(h - 1, min(y0, y1, y2)))
                ymax = max(0, min(h - 1, max(y0, y1, y2)))

                for py in range(ymin, ymax + 1):
                    for px in range(xmin, xmax + 1):
                        e01 = (px - x0) * (y1 - y0) - (py - y0) * (x1 - x0)
                        e12 = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
                        e20 = (px - x2) * (y0 - y2) - (py - y2) * (x0 - x2)
                        if (e01 >= 0 and e12 >= 0 and e20 >= 0) or (e01 <= 0 and e12 <= 0 and e20 <= 0):
                            idx = (py * w + px) * 4
                            if blend_mode == 'ADD':
                                raw_pixels[idx] = min(1.0, raw_pixels[idx] + fill_rgba[0])
                                raw_pixels[idx+1] = min(1.0, raw_pixels[idx+1] + fill_rgba[1])
                                raw_pixels[idx+2] = min(1.0, raw_pixels[idx+2] + fill_rgba[2])
                            elif blend_mode == 'MULTIPLY':
                                raw_pixels[idx] *= fill_rgba[0]
                                raw_pixels[idx+1] *= fill_rgba[1]
                                raw_pixels[idx+2] *= fill_rgba[2]
                            else:
                                raw_pixels[idx] = fill_rgba[0]
                                raw_pixels[idx+1] = fill_rgba[1]
                                raw_pixels[idx+2] = fill_rgba[2]
                                raw_pixels[idx+3] = fill_rgba[3]
        img.pixels[:] = raw_pixels

    img.update()
    if is_edit:
        bmesh.update_edit_mesh(obj.data)
    else:
        bm.free()
    return len(selected_faces)

# -------------------------------------------------------------------
# RGB Shadow Map → ILM Converter  (Mihoyo / Genshin-Impact style)
# R = outline mask  G = shadow threshold  B = specular intensity  A = rim/unused
# -------------------------------------------------------------------

def convert_rgb_shadow_to_ilm(shadow_img, ilm_img,
                               spec_threshold=0.85, rim_threshold=0.15,
                               invert_shadow=False):
    """Convert a Mihoyo/GI-style RGB shadow map into the ILM RGBA channel-packed format.

    ILM channel mapping result:
      R = shadow offset (from GI.G shadow threshold channel, remapped to 0-1)
      G = emission mask (derived from specular peaks above spec_threshold)
      B = specular mask (GI.B channel, direct)
      A = rim mask     (GI.A channel if present, else Sobel-approx from GI.G edges)

    Args:
        shadow_img  : source bpy.types.Image (Mihoyo RGB shadow map)
        ilm_img     : destination bpy.types.Image (will be overwritten)
        spec_threshold  : luminance cutoff above which B-channel counts as specular peak
        rim_threshold   : gradient magnitude threshold for rim extraction from G-channel
        invert_shadow   : if True, flip the G-channel polarity (dark = lit)
    """
    if shadow_img is None or ilm_img is None:
        raise ValueError("Both source shadow_img and destination ilm_img must be provided.")

    # Ensure source image has pixel data
    try:
        _ = shadow_img.pixels[0]
    except Exception:
        try:
            shadow_img.reload()
        except Exception:
            pass

    src_w, src_h = shadow_img.size
    if src_w == 0 or src_h == 0:
        raise ValueError("Source shadow image has no pixel data (size 0).")

    dst_w, dst_h = ilm_img.size
    if dst_w == 0 or dst_h == 0:
        # Scale destination to match source
        ilm_img.source = 'GENERATED'
        ilm_img.generated_width = src_w
        ilm_img.generated_height = src_h
        ilm_img.update()
        dst_w, dst_h = src_w, src_h

    px_cnt = dst_w * dst_h

    # Read source channels (resize if needed)
    ch_r = get_resized_channel(shadow_img, dst_w, dst_h, "R")   # outline mask (discard)
    ch_g = get_resized_channel(shadow_img, dst_w, dst_h, "G")   # shadow threshold → ILM.R
    ch_b = get_resized_channel(shadow_img, dst_w, dst_h, "B")   # specular intensity → ILM.B
    ch_a = get_resized_channel(shadow_img, dst_w, dst_h, "A")   # rim mask → ILM.A

    if np is not None:
        ch_g_arr = np.array(ch_g if ch_g is not None else [0.5] * px_cnt, dtype=np.float32)
        ch_b_arr = np.array(ch_b if ch_b is not None else [0.0] * px_cnt, dtype=np.float32)
        ch_a_arr = np.array(ch_a if ch_a is not None else [0.0] * px_cnt, dtype=np.float32)

        # ILM.R = shadow channel (GI G-channel already encodes shadow softness)
        # Mihoyo: value 0 = fully shadowed, value 1 = never shadowed
        # The shader expects: 0.5 = neutral (follows real-time light), so we keep G as-is
        ilm_r = np.clip(ch_g_arr if not invert_shadow else (1.0 - ch_g_arr), 0.0, 1.0)

        # ILM.G = emission mask: pixels where specular B-channel exceeds spec_threshold
        # This lets very bright spec areas also glow subtly
        ilm_g = np.clip((ch_b_arr - spec_threshold) / max(1.0 - spec_threshold, 1e-6), 0.0, 1.0)

        # ILM.B = specular mask (direct from GI.B)
        ilm_b = np.clip(ch_b_arr, 0.0, 1.0)

        # ILM.A = rim mask
        # If the source alpha channel has meaningful variation, use it directly.
        # Otherwise, approximate rim from gradient of shadow channel via finite diff.
        if ch_a is not None and float(np.std(ch_a_arr)) > 0.01:
            ilm_a = np.clip(ch_a_arr, 0.0, 1.0)
        else:
            g2d = ch_g_arr.reshape((dst_h, dst_w))
            gx = np.zeros_like(g2d); gy = np.zeros_like(g2d)
            gx[:, 1:-1] = g2d[:, 2:] - g2d[:, :-2]
            gy[1:-1, :] = g2d[2:, :] - g2d[:-2, :]
            grad_mag = np.sqrt(gx ** 2 + gy ** 2).ravel()
            ilm_a = np.clip((grad_mag - rim_threshold) / max(1.0 - rim_threshold, 1e-6), 0.0, 1.0)

        out_arr = np.empty((px_cnt, 4), dtype=np.float32)
        out_arr[:, 0] = ilm_r; out_arr[:, 1] = ilm_g
        out_arr[:, 2] = ilm_b; out_arr[:, 3] = ilm_a
        try:
            ilm_img.pixels.foreach_set(out_arr.ravel())
        except Exception:
            ilm_img.pixels[:] = out_arr.ravel().tolist()

    else:
        def safe_ch(ch, default, idx):
            if ch is None: return default
            return float(ch[idx]) if idx < len(ch) else default
        flat = []
        for i in range(px_cnt):
            g_val = safe_ch(ch_g, 0.5, i); b_val = safe_ch(ch_b, 0.0, i)
            a_val = safe_ch(ch_a, 0.0, i)
            r_out = min(1.0, max(0.0, (1.0 - g_val) if invert_shadow else g_val))
            g_out = min(1.0, max(0.0, (b_val - spec_threshold) / max(1.0 - spec_threshold, 1e-6)))
            b_out = min(1.0, max(0.0, b_val))
            a_out = min(1.0, max(0.0, a_val))
            flat.extend([r_out, g_out, b_out, a_out])
        ilm_img.pixels[:] = flat

    set_image_colorspace(ilm_img, MASK_COLORSPACE)
    set_channel_packed_alpha(ilm_img)
    try:
        ilm_img.update()
        ilm_img.pack()
    except Exception:
        pass


def convert_colorzone_shadow_to_ilm(shadow_img, ilm_img,
                                     spec_threshold=0.85, rim_threshold=0.15,
                                     invert_shadow=False):
    """Convert a flat-color shadow zone map into the ILM RGBA channel-packed format.

    This mode handles shadow maps where each material zone is painted as a flat color —
    e.g. pinkish skin = lit zone, dark/black = shadow zone.
    RGB luminance of each pixel drives the shadow threshold.
    Zone color boundaries become rim light areas via Sobel edge detection.

    ILM channel mapping result:
      R = shadow threshold  (perceptual BT.709 luminance: bright = lit, dark = shadowed)
      G = emission hint     (extreme luminance peaks above 0.92)
      B = specular mask     (high-lum + low-saturation = near-white highlight areas)
      A = rim mask          (Sobel gradient of luminance = zone boundary transitions)

    Args:
        shadow_img      : source bpy.types.Image (flat-color zone shadow map)
        ilm_img         : destination bpy.types.Image (will be overwritten)
        spec_threshold  : lum×(1-sat) value above which pixels contribute to ILM.B specular
        rim_threshold   : Sobel gradient percentile below which rim contribution is zeroed
        invert_shadow   : if True, flip luminance polarity (dark regions = lit)
    """
    if shadow_img is None or ilm_img is None:
        raise ValueError("Both source shadow_img and destination ilm_img must be provided.")

    try:
        _ = shadow_img.pixels[0]
    except Exception:
        try:
            shadow_img.reload()
        except Exception:
            pass

    src_w, src_h = shadow_img.size
    if src_w == 0 or src_h == 0:
        raise ValueError("Source shadow image has no pixel data (size 0).")

    dst_w, dst_h = ilm_img.size
    if dst_w == 0 or dst_h == 0:
        ilm_img.source = 'GENERATED'
        ilm_img.generated_width = src_w
        ilm_img.generated_height = src_h
        ilm_img.update()
        dst_w, dst_h = src_w, src_h

    px_cnt = dst_w * dst_h

    ch_r = get_resized_channel(shadow_img, dst_w, dst_h, "R")
    ch_g = get_resized_channel(shadow_img, dst_w, dst_h, "G")
    ch_b_ch = get_resized_channel(shadow_img, dst_w, dst_h, "B")

    if np is not None:
        r_arr = np.array(ch_r if ch_r is not None else [0.0] * px_cnt, dtype=np.float32)
        g_arr = np.array(ch_g if ch_g is not None else [0.0] * px_cnt, dtype=np.float32)
        b_arr = np.array(ch_b_ch if ch_b_ch is not None else [0.0] * px_cnt, dtype=np.float32)

        # --- ILM.R: Shadow threshold from perceptual BT.709 luminance ---
        # Bright zones = lit areas (ILM.R → 1.0), dark/black = shadow (ILM.R → 0.0)
        lum = r_arr * 0.2126 + g_arr * 0.7152 + b_arr * 0.0722
        # Stretch to actual content range, ignoring border outliers
        lum_min = float(np.percentile(lum, 2))
        lum_max = float(np.percentile(lum, 98))
        lum_norm = np.clip((lum - lum_min) / max(lum_max - lum_min, 1e-6), 0.0, 1.0)
        ilm_r = (1.0 - lum_norm) if invert_shadow else lum_norm

        # --- ILM.B: Specular mask from bright + desaturated pixels ---
        # Near-white hotspots: high luminance AND low saturation = specular highlight
        max_rgb = np.maximum(np.maximum(r_arr, g_arr), b_arr)
        min_rgb = np.minimum(np.minimum(r_arr, g_arr), b_arr)
        saturation = np.where(max_rgb > 1e-6, (max_rgb - min_rgb) / max_rgb, 0.0)
        spec_signal = lum_norm * (1.0 - saturation)
        ilm_b = np.clip((spec_signal - spec_threshold) / max(1.0 - spec_threshold, 1e-6), 0.0, 1.0)

        # --- ILM.G: Emission hint from the very brightest near-white areas ---
        ilm_g = np.clip((lum_norm - 0.92) / 0.08, 0.0, 1.0)

        # --- ILM.A: Rim mask from Sobel gradient across zone boundaries ---
        # Where the color changes sharply (lit → shadow zone edge) = model rim / silhouette
        lum2d = lum_norm.reshape((dst_h, dst_w))
        gx2 = np.zeros_like(lum2d)
        gy2 = np.zeros_like(lum2d)
        gx2[:, 1:-1] = lum2d[:, 2:] - lum2d[:, :-2]
        gy2[1:-1, :] = lum2d[2:, :] - lum2d[:-2, :]
        grad_mag = np.sqrt(gx2 ** 2 + gy2 ** 2).ravel()
        # Normalize to the 99th percentile gradient to avoid outlier spikes
        grad_top = float(np.percentile(grad_mag, 99))
        grad_norm = np.clip(grad_mag / max(grad_top, 1e-6), 0.0, 1.0)
        ilm_a = np.clip((grad_norm - rim_threshold) / max(1.0 - rim_threshold, 1e-6), 0.0, 1.0)

        out_arr = np.empty((px_cnt, 4), dtype=np.float32)
        out_arr[:, 0] = ilm_r
        out_arr[:, 1] = ilm_g
        out_arr[:, 2] = ilm_b
        out_arr[:, 3] = ilm_a
        try:
            ilm_img.pixels.foreach_set(out_arr.ravel())
        except Exception:
            ilm_img.pixels[:] = out_arr.ravel().tolist()

    else:
        # Pure-Python fallback (no numpy)
        def safe(ch, idx, default=0.0):
            if ch is None: return default
            return float(ch[idx]) if idx < len(ch) else default

        flat = []
        for i in range(px_cnt):
            rv = safe(ch_r, i); gv = safe(ch_g, i); bv = safe(ch_b_ch, i)
            lum_v = rv * 0.2126 + gv * 0.7152 + bv * 0.0722
            ilm_r_v = min(1.0, max(0.0, (1.0 - lum_v) if invert_shadow else lum_v))
            max_v = max(rv, gv, bv, 1e-6)
            sat_v = (max_v - min(rv, gv, bv)) / max_v
            spec_v = min(1.0, max(0.0, (lum_v * (1.0 - sat_v) - spec_threshold) / max(1.0 - spec_threshold, 1e-6)))
            ilm_g_v = min(1.0, max(0.0, (lum_v - 0.92) / 0.08))
            flat.extend([ilm_r_v, ilm_g_v, spec_v, 0.0])  # rim=0 in Python fallback
        ilm_img.pixels[:] = flat

    set_image_colorspace(ilm_img, MASK_COLORSPACE)
    set_channel_packed_alpha(ilm_img)
    try:
        ilm_img.update()
        ilm_img.pack()
    except Exception:
        pass


def get_resized_channel(src_img, target_w, target_h, channel):
    if not is_valid_image(src_img):
        return None
        
    arr = image_channel_array(src_img, channel)
    if arr is None:
        return None
        
    src_w, src_h = src_img.size
    if src_w == target_w and src_h == target_h:
        return arr
        
    if np is not None and isinstance(arr, np.ndarray):
        y_idx = np.linspace(0, src_h - 1, target_h).astype(np.int64)
        x_idx = np.linspace(0, src_w - 1, target_w).astype(np.int64)
        grid = arr.reshape((src_h, src_w))
        resized = grid[np.ix_(y_idx, x_idx)]
        return resized.ravel()
    else:
        grid = [arr[y * src_w:(y + 1) * src_w] for y in range(src_h)]
        out = []
        for ty in range(target_h):
            sy = int(ty * (src_h - 1) / max(1, target_h - 1))
            row = grid[sy]
            for tx in range(target_w):
                sx = int(tx * (src_w - 1) / max(1, target_w - 1))
                out.append(row[sx])
        return out

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
        if is_valid_image(src):
            try:
                _ = src.pixels[0]
            except Exception: pass
            if src.size[0] > 0 and src.size[1] > 0:
                target_size = src.size[:]
                break

    if target_size is None:
        target_size = (DEFAULT_SIZE, DEFAULT_SIZE)

    target_w, target_h = target_size
    cs = dst_img.colorspace_settings.name if getattr(dst_img, "colorspace_settings", None) else MASK_COLORSPACE

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

    # Configure colorspace and alpha BEFORE setting pixel buffers
    set_image_colorspace(dst_img, cs)
    set_channel_packed_alpha(dst_img)

    try: _ = dst_img.pixels[0]
    except: pass

    px_cnt = target_w * target_h

    def get_ch(src, ch, def_val):
        if not is_valid_image(src):
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
        for sock in collection:
            # Mix exposes FLOAT, VECTOR and RGBA sockets with identical names.
            # Selecting the first 'A' silently wires color into the inactive float input.
            if sock.name == nm and sock.enabled: return sock
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
        "DETAIL_EMISSION": "Detail_Emission",
        "PATTERN_MASK": "Pattern Mask"
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
    if scene.world and hasattr(scene.world, "light_settings") and hasattr(scene.world.light_settings, "distance"):
        try: state["world.ao_distance"] = scene.world.light_settings.distance
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
    if "world.ao_distance" in state and scene.world and hasattr(scene.world, "light_settings"):
        try: scene.world.light_settings.distance = state["world.ao_distance"]
        except Exception: pass

def configure_internal_bake(scene, samples, is_ao=False):
    enable_gpu_cycles(scene)
    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        try: cycles.samples = samples
        except Exception: pass
    if is_ao and scene.world and hasattr(scene.world, "light_settings") and hasattr(scene.world.light_settings, "distance"):
        try: scene.world.light_settings.distance = 0.35 # Realistic anime character contact AO distance in meters
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

    # USER RULE ENFORCEMENT: Never use Cycles bake, always use Eevee camera live bake.
    success = _bake_material_via_live_camera(context, obj, mat, img)

    if success and pack_after:
        try: img.pack()
        except Exception: pass
        
    return success

def material_base_name(mat):
    if mat is None:
        return "AnimeToon"
    return mat.name.replace("_AnimeToon", "")

def detect_shader_type_for_material(mat):
    if not mat:
        return 'DEFAULT'
    existing = mat.get("genos_shader_type")
    if existing in ('FACE', 'HAIR', 'METALLIC'):
        return existing
    name = mat.name.lower()
    if any(k in name for k in ("face", "head", "skin", "cheek", "mouth", "lip", "eye", "brow", "lash", "teeth", "tongue")):
        return 'FACE'
    if any(k in name for k in ("hair", "bang", "ahoge", "twintail", "ponytail", "fur", "scalp", "braid", "strand")):
        return 'HAIR'
    if any(k in name for k in ("metal", "armor", "mech", "weapon", "shield", "blade", "gun")):
        return 'METALLIC'
    return existing if existing else 'DEFAULT'

def material_node_image(mat, node_name, *, colorspace=None):
    if not mat or not mat.use_nodes:
        return None
    node = mat.node_tree.nodes.get(node_name)
    img = node.image if node and hasattr(node, "image") else None
    if img:
        try: img.update()
        except Exception: pass
        if colorspace and getattr(img, 'colorspace_settings', None) and img.colorspace_settings.name != colorspace:
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
    if img is not None:
        ensure_image_data(img, None, scene_texture_size(), scene_texture_size())
    if img is None or not is_valid_image(img):
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

def configure_standard_map_image(img, map_kind):
    """Apply consistent color-management/storage rules for common texture maps."""
    if img is None:
        return None
    kind = str(map_kind or '').upper()
    color_kinds = {'BASECOLOR', 'ALBEDO', 'EMISSION'}
    cs = 'sRGB' if kind in color_kinds else MASK_COLORSPACE
    set_image_colorspace(img, cs)
    try:
        img.use_fake_user = True
    except Exception:
        pass
    if kind in {'NORMAL', 'DISPLACEMENT', 'HEIGHT', 'ROUGHNESS', 'METALLIC', 'AO', 'OPACITY', 'MASK', 'SDF', 'ILM', 'DETAIL'}:
        try:
            img.alpha_mode = 'CHANNEL_PACKED'
        except Exception:
            pass
    return img


def _map_texture_node(nodes, name, img, location, map_kind, interpolation='Linear'):
    if not is_valid_image(img):
        return None
    configure_standard_map_image(img, map_kind)
    tex = make_node(nodes, 'ShaderNodeTexImage', name, location)
    tex.image = img
    tex.interpolation = interpolation
    try:
        tex.extension = 'REPEAT'
        tex.projection = 'FLAT'
    except Exception:
        pass
    return tex


def _normal_color_socket(nodes, links, tex_node, scene, loc=(-1700, -900)):
    """Return an OpenGL tangent-space normal color socket.

    DirectX normal maps are converted live by inverting only the green/Y channel,
    leaving the underlying source image untouched.
    """
    if tex_node is None:
        return None
    convention = getattr(scene, 'genos_normal_convention', 'OPENGL') if scene else 'OPENGL'
    if convention != 'DIRECTX':
        return tex_node.outputs['Color']
    sep = make_node(nodes, 'ShaderNodeSeparateColor', 'Normal DX Split', (loc[0], loc[1] - 120))
    inv = make_node(nodes, 'ShaderNodeMath', 'Normal DX Invert Y', (loc[0] + 180, loc[1] - 120))
    inv.operation = 'SUBTRACT'
    inv.inputs[0].default_value = 1.0
    comb = make_node(nodes, 'ShaderNodeCombineColor', 'Normal DX to GL', (loc[0] + 360, loc[1] - 120))
    link(links, tex_node.outputs['Color'], sep.inputs[0])
    link(links, sep.outputs[1], inv.inputs[1])
    link(links, sep.outputs[0], comb.inputs[0])
    link(links, inv.outputs[0], comb.inputs[1])
    link(links, sep.outputs[2], comb.inputs[2])
    return comb.outputs[0]


def build_surface_detail_chain(nodes, links, scene, output_node, normal_img=None, displacement_img=None, loc=(-1800, -900)):
    """Build one shared normal + height/displacement chain for preview and baked shaders.

    Tangent normal is applied first, then height as bump for Eevee/viewport. When
    enabled, the same height map is also connected to Material Output displacement
    through a true Displacement node for Cycles subdivision workflows.
    """
    normal_map = make_node(nodes, 'ShaderNodeNormalMap', 'Normal Map', (loc[0] + 300, loc[1]))
    try:
        normal_map.space = 'TANGENT'
        normal_map.inputs['Strength'].default_value = float(getattr(scene, 'genos_normal_strength', 1.0))
    except Exception:
        pass

    ntex = _map_texture_node(nodes, 'Normal_Tex', normal_img, (loc[0], loc[1]), 'NORMAL')
    ncolor = _normal_color_socket(nodes, links, ntex, scene, loc=(loc[0] + 50, loc[1])) if ntex else None
    if ncolor is not None:
        link(links, ncolor, normal_map.inputs['Color'])

    bump = make_node(nodes, 'ShaderNodeBump', 'Displacement Bump', (loc[0] + 520, loc[1]))
    try:
        bump.inputs['Strength'].default_value = 1.0
        bump.inputs['Distance'].default_value = float(getattr(scene, 'genos_displacement_strength', 0.1))
        bump.inputs['Midlevel'].default_value = float(getattr(scene, 'genos_displacement_midlevel', 0.5))
    except Exception:
        pass
    link(links, normal_map.outputs['Normal'], bump.inputs['Normal'])

    dtex = _map_texture_node(nodes, 'Displacement Map', displacement_img, (loc[0], loc[1] + 190), 'DISPLACEMENT')
    if dtex is not None:
        rgb2bw = make_node(nodes, 'ShaderNodeRGBToBW', 'Displacement Luminance', (loc[0] + 220, loc[1] + 190))
        link(links, dtex.outputs['Color'], rgb2bw.inputs[0])
        link(links, rgb2bw.outputs[0], bump.inputs['Height'])

        if bool(getattr(scene, 'genos_true_displacement', False)) and output_node is not None and output_node.inputs.get('Displacement'):
            disp = make_node(nodes, 'ShaderNodeDisplacement', 'True Displacement', (loc[0] + 520, loc[1] + 210))
            link(links, rgb2bw.outputs[0], disp.inputs['Height'])
            try:
                disp.inputs['Midlevel'].default_value = float(getattr(scene, 'genos_displacement_midlevel', 0.5))
                disp.inputs['Scale'].default_value = float(getattr(scene, 'genos_displacement_strength', 0.1))
            except Exception:
                pass
            link(links, disp.outputs['Displacement'], output_node.inputs['Displacement'])

    return bump.outputs['Normal']


def _copy_image_pixels(src, dst):
    if not is_valid_image(src) or dst is None:
        return False
    try:
        sw, sh = int(src.size[0]), int(src.size[1])
        if sw <= 0 or sh <= 0:
            return False
        if int(dst.size[0]) != sw or int(dst.size[1]) != sh:
            dst.scale(sw, sh)
        if np is not None:
            buf = np.empty(sw * sh * 4, dtype=np.float32)
            src.pixels.foreach_get(buf)
            dst.pixels.foreach_set(buf)
        else:
            dst.pixels[:] = list(src.pixels[:])
        dst.update()
        return True
    except Exception:
        return False


def _temporary_bake_target(base_name, suffix, *, normal=False, reference_img=None):
    size = scene_texture_size()
    width = height = size
    if is_valid_image(reference_img):
        try:
            width = max(1, int(reference_img.size[0]))
            height = max(1, int(reference_img.size[1]))
        except Exception:
            width = height = size
    return bpy.data.images.new(name=f'__GENOS_TMP_{base_name}_{suffix}', width=width, height=height, alpha=False, float_buffer=False)


def _bake_temp_material_pass(context, obj, source_mat, target_img, pass_type='EMIT', source_node_name=None, source_output='Color', source_input_node_name=None, source_input='Height', prefill=(0.0,0.0,0.0,1.0)):
    """Bake through a copied material and a separate target image to avoid feedback.

    For data maps, source_node_name routes the named node output through Emission;
    NORMAL uses Blender's real tangent-space normal bake pass on the copied shader.
    """
    if not obj or not source_mat or not target_img:
        return False
    # Bake targets for normals/masks/height are data textures; keep values linear.
    try: configure_standard_map_image(target_img, 'NORMAL' if pass_type == 'NORMAL' else 'MASK')
    except Exception: pass
    temp_mat = source_mat.copy()
    temp_mat.name = '__GENOS_TMP_MAP_BAKE'
    temp_mat.use_nodes = True
    nodes = temp_mat.node_tree.nodes
    links = temp_mat.node_tree.links

    out = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL' and getattr(n, 'is_active_output', True)), None)
    if out is None:
        out = nodes.new('ShaderNodeOutputMaterial')

    if pass_type == 'NORMAL':
        # Route a BSDF that actually consumes the addon's final normal chain so the
        # tangent normal bake includes both normal-map and height/bump detail.
        capture = nodes.get('Scene Light Capture')
        if capture is None or not hasattr(capture, 'outputs'):
            capture = nodes.new('ShaderNodeBsdfDiffuse')
            nnode = nodes.get('Displacement Bump') or nodes.get('Normal Map')
            if nnode and nnode.outputs.get('Normal'):
                links.new(nnode.outputs['Normal'], capture.inputs['Normal'])
        for l in list(out.inputs['Surface'].links):
            links.remove(l)
        links.new(capture.outputs[0], out.inputs['Surface'])

    if pass_type == 'EMIT' and (source_node_name or source_input_node_name):
        sock = None
        if source_input_node_name:
            inp_node = nodes.get(source_input_node_name)
            inp = inp_node.inputs.get(source_input) if inp_node and hasattr(inp_node, 'inputs') else None
            if inp and inp.links:
                sock = inp.links[0].from_socket
            elif inp is not None:
                val = nodes.new('ShaderNodeValue')
                try: val.outputs[0].default_value = float(inp.default_value)
                except Exception: val.outputs[0].default_value = 0.0
                sock = val.outputs[0]
        if sock is None and source_node_name:
            src = nodes.get(source_node_name)
            if src is not None:
                sock = src.outputs.get(source_output) if hasattr(src, 'outputs') else None
                if sock is None and hasattr(src, 'outputs') and len(src.outputs):
                    sock = src.outputs[0]
        if sock is None:
            bpy.data.materials.remove(temp_mat)
            return False
        emit = nodes.new('ShaderNodeEmission')
        if getattr(sock, 'type', '') in {'VALUE', 'INT', 'BOOLEAN'}:
            comb = nodes.new('ShaderNodeCombineColor')
            links.new(sock, comb.inputs[0]); links.new(sock, comb.inputs[1]); links.new(sock, comb.inputs[2])
            links.new(comb.outputs[0], emit.inputs['Color'])
        else:
            links.new(sock, emit.inputs['Color'])
        # Replace only the copied material's surface output.
        for l in list(out.inputs['Surface'].links):
            links.remove(l)
        links.new(emit.outputs[0], out.inputs['Surface'])

    target_node = nodes.new('ShaderNodeTexImage')
    target_node.name = '__GENOS_BAKE_TARGET'
    target_node.image = target_img
    nodes.active = target_node
    for n in nodes:
        n.select = False
    target_node.select = True

    orig_mats = [slot.material for slot in obj.material_slots]
    orig_index = obj.active_material_index
    orig_mode = obj.mode
    bake_state = capture_bake_state(context.scene)
    success = False
    try:
        if orig_mode != 'OBJECT':
            try: bpy.ops.object.mode_set(mode='OBJECT')
            except Exception: pass
        for slot in obj.material_slots:
            slot.material = temp_mat
        context.view_layer.objects.active = obj
        obj.select_set(True)
        fill_image_solid(target_img, prefill, int(target_img.size[0]), int(target_img.size[1]))
        configure_internal_bake(context.scene, 128)
        success = bool(bake_active_image(pass_type, margin=16, use_clear=False))
        if success:
            target_img.update()
    finally:
        restore_bake_state(context.scene, bake_state)
        for i, slot in enumerate(obj.material_slots):
            if i < len(orig_mats):
                slot.material = orig_mats[i]
        obj.active_material_index = orig_index
        if orig_mode != 'OBJECT':
            try: bpy.ops.object.mode_set(mode=orig_mode)
            except Exception: pass
        bpy.data.materials.remove(temp_mat)
    return success


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

    scene = bpy.context.scene if bpy.context else None
    base_preview_color = find_socket(accent_add.outputs, "Result", "Color")

    shaded_color, light_step_1, light_step_2 = _build_3band_cel_shading(
        nodes, links, scene, shadow_add.outputs[0], base_preview_color, loc=(-700, 250)
    )

    spec_gain = make_node(nodes, "ShaderNodeMath", "Preview Spec Gain", (-700, -100))
    spec_gain.operation = 'MULTIPLY'
    link(links, ilm_spec.outputs[0], spec_gain.inputs[0])
    spec_gain.inputs[1].default_value = 0.8

    spec_add = make_node(nodes, "ShaderNodeMix", "Preview Add Spec", (-200, 350))
    spec_add.data_type = 'RGBA'
    spec_add.blend_type = 'ADD'
    link(links, spec_gain.outputs[0], find_socket(spec_add.inputs, "Factor", "Fac"))
    link(links, shaded_color, find_socket(spec_add.inputs, "A", "Color1"))
    find_socket(spec_add.inputs, "B", "Color2").default_value = tuple(getattr(scene, "genos_spec_tint", (1.0, 1.0, 1.0, 1.0)))

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
    link(links, base_preview_color, find_socket(glow_color.inputs, "B", "Color2"))

    tinted_glow = make_node(nodes, "ShaderNodeMix", "Preview Tint Glow", (-300, -550))
    tinted_glow.data_type = 'RGBA'
    tinted_glow.blend_type = 'MULTIPLY'
    find_socket(tinted_glow.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(glow_color.outputs, "Result", "Color"), find_socket(tinted_glow.inputs, "A", "Color1"))
    find_socket(tinted_glow.inputs, "B", "Color2").default_value = tuple(getattr(scene, "genos_emission_tint", (1.0, 0.45, 0.1, 1.0)))

    total_emission = make_node(nodes, "ShaderNodeMix", "Preview Total Emission", (-100, -500))
    total_emission.data_type = 'RGBA'
    total_emission.blend_type = 'ADD'
    find_socket(total_emission.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(tinted_glow.outputs, "Result", "Color"), find_socket(total_emission.inputs, "A", "Color1"))
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
    find_socket(rim_add.inputs, "B", "Color2").default_value = tuple(getattr(scene, "genos_rim_color", (0.90, 0.94, 1.0, 1.0)))

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
    if "genos_shader_type" not in mat:
        mat["genos_shader_type"] = detect_shader_type_for_material(mat)
    shader_type = mat.get("genos_shader_type", "DEFAULT")

    try:
        if shader_type == 'HAIR':
            mat.blend_method = 'HASHED'
            mat.shadow_method = 'CLIP'
            mat.show_transparent_back = False
        else:
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
        if not is_valid_image(img):
            if key == "basecolor" and is_valid_image(getattr(mat, "genos_base_color_map", None)):
                img = mat.genos_base_color_map
            elif key == "normal_map" and is_valid_image(getattr(mat, "genos_normal_map", None)):
                img = mat.genos_normal_map
            elif key == "emission_map" and is_valid_image(getattr(mat, "genos_emission_map", None)):
                img = mat.genos_emission_map
            else:
                existing = bpy.data.images.get(f"{base}_{suffix}")
                if is_valid_image(existing):
                    img = existing
                else:
                    img = make_image(f"{base}_{suffix}", DEFAULT_SIZE, DEFAULT_SIZE, alpha=use_alpha, colorspace=cs, color=color)
        set_image_colorspace(img, cs)
        try: img.use_fake_user = True
        except Exception: pass
        return img

    safe_basecolor = get_safe("basecolor", "BaseColor", (0.8, 0.8, 0.8, 1.0), "sRGB", True)
    safe_emission = get_safe("emission_map", "EmissionMap", (0.0, 0.0, 0.0, 1.0), "sRGB", True)
    try:
        mat.genos_base_color_map = safe_basecolor
        mat.genos_emission_map = safe_emission
    except Exception:
        pass
    safe_shadow = get_safe("ilm_shadow", "ILM_ShadowSrc", (0.5, 0.5, 0.5, 1.0), MASK_COLORSPACE, True)
    safe_ilm_emit = get_safe("ilm_emission", "ILM_EmissionSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_spec = get_safe("ilm_spec", "ILM_SpecSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_rim = get_safe("ilm_rim", "ILM_RimSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_ao = get_safe("detail_ao", "Detail_AOSrc", (1.0, 1.0, 1.0, 1.0), MASK_COLORSPACE, True) 
    safe_curve = get_safe("detail_curve", "Detail_CurveSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_accent = get_safe("detail_accent", "Detail_AccentSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_det_emit = get_safe("detail_emission", "Detail_EmissionSrc", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    safe_pattern_mask = get_safe("pattern_mask", "PatternMask", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE, True)
    
    # Retrieve from images dict first to handle garbage collection survivability during nodes.clear()
    safe_pattern_color = images.get("pattern_color")
    if safe_pattern_color is None:
        safe_pattern_color = getattr(mat, "genos_pattern_color_map", None)
        
    if safe_pattern_color:
        set_image_colorspace(safe_pattern_color, "sRGB")
    safe_disp = get_safe("displacement_map", "Displacement", (0.5, 0.5, 0.5, 1.0), "Non-Color", False)

    # Optional standard PBR/data maps are not auto-created here. Missing maps use
    # cheap constants, keeping the realtime graph lighter for materials that do not need them.
    def optional_map(key, prop_name, kind):
        img = images.get(key) or getattr(mat, prop_name, None)
        if is_valid_image(img):
            configure_standard_map_image(img, kind)
            return img
        return None

    safe_normal = optional_map('normal_map', 'genos_normal_map', 'NORMAL')
    safe_roughness = optional_map('roughness_map', 'genos_roughness_map', 'ROUGHNESS')
    safe_metallic = optional_map('metallic_map', 'genos_metallic_map', 'METALLIC')
    safe_opacity = optional_map('opacity_map', 'genos_opacity_map', 'OPACITY')
    safe_ao_map = optional_map('ao_map', 'genos_ao_map', 'AO')
    try:
        mat.genos_displacement_map = safe_disp
    except Exception:
        pass

    output = make_node(nodes, "ShaderNodeOutputMaterial", "Material Output", (3000, 0))
    mix_shader = make_node(nodes, "ShaderNodeMixShader", "Alpha Blend", (2700, 0))
    emission_out = make_node(nodes, "ShaderNodeEmission", "Anime Terminal Output", (2400, 0))
    transparent_bsdf = make_node(nodes, "ShaderNodeBsdfTransparent", "Transparency", (2400, -300))
    
    strength_val = make_node(nodes, "ShaderNodeValue", "Global Emission Strength", (2400, -150))
    strength_val.outputs[0].default_value = 1.0 
    
    link(links, strength_val.outputs[0], emission_out.inputs["Strength"])
    link(links, transparent_bsdf.outputs[0], mix_shader.inputs[1]) 
    link(links, emission_out.outputs[0], mix_shader.inputs[2])     
    link(links, mix_shader.outputs[0], output.inputs["Surface"])

    base_tex = make_node(nodes, "ShaderNodeTexImage", "BaseColor", (-1500, 400))
    base_tex.image = safe_basecolor
    base_tex.interpolation = 'Smart'
    
    alpha_source = base_tex.outputs['Alpha']
    opacity_tex = _map_texture_node(nodes, 'Opacity Map', safe_opacity, (-1500, 520), 'OPACITY')
    if opacity_tex is not None:
        opacity_bw = make_node(nodes, 'ShaderNodeRGBToBW', 'Opacity Luminance', (-1320, 520))
        opacity_mul = make_node(nodes, 'ShaderNodeMath', 'Base Alpha x Opacity', (-1140, 520))
        opacity_mul.operation = 'MULTIPLY'
        link(links, opacity_tex.outputs['Color'], opacity_bw.inputs[0])
        link(links, base_tex.outputs['Alpha'], opacity_mul.inputs[0])
        link(links, opacity_bw.outputs[0], opacity_mul.inputs[1])
        alpha_source = opacity_mul.outputs[0]

    alpha_clip_gate = make_node(nodes, "ShaderNodeMath", "Alpha Failsafe", (2300, 200))
    alpha_clip_gate.operation = 'GREATER_THAN'
    alpha_clip_gate.inputs[1].default_value = 0.1
    link(links, alpha_source, alpha_clip_gate.inputs[0])

    if shader_type == 'HAIR':
        hair_transparency_val = make_node(nodes, "ShaderNodeValue", "Hair Transparency", (2500, 100))
        hair_transparency_val.outputs[0].default_value = getattr(bpy.context.scene, "genos_hair_transparency", 0.5)

        # Depth-based alpha masking for eye transparency (Hair over Eyes effect)
        light_path = make_node(nodes, "ShaderNodeLightPath", "Light Path", (2300, 400))
        transparent_depth = make_node(nodes, "ShaderNodeMath", "Is Transparent Depth", (2450, 400))
        transparent_depth.operation = 'GREATER_THAN'
        link(links, light_path.outputs["Transparent Depth"], transparent_depth.inputs[0])
        transparent_depth.inputs[1].default_value = 0.0
        
        # Mix base alpha config with the transparency blend whenever depth check hits
        depth_mix = make_node(nodes, "ShaderNodeMix", "Depth Mix Alpha", (2600, 300))
        depth_mix.data_type = 'FLOAT'
        depth_mix.blend_type = 'MIX'
        link(links, transparent_depth.outputs[0], depth_mix.inputs["Factor"])
        link(links, hair_transparency_val.outputs[0], depth_mix.inputs["B"])
        depth_mix.inputs["A"].default_value = 1.0 # 1.0 alpha normal case

        # Multiplied overall hair alpha factor
        hair_alpha_factor = make_node(nodes, "ShaderNodeMath", "Hair Alpha Factor", (2700, 150))
        hair_alpha_factor.operation = 'MULTIPLY'
        link(links, alpha_clip_gate.outputs[0], hair_alpha_factor.inputs[0])
        link(links, depth_mix.outputs["Result"], hair_alpha_factor.inputs[1])
        link(links, hair_alpha_factor.outputs[0], mix_shader.inputs[0])
    else:
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

    pattern_mask = make_node(nodes, "ShaderNodeTexImage", "Pattern Mask", (-1500, -780))
    pattern_mask.image = safe_pattern_mask
    pattern_mask.interpolation = 'Linear'

    surface_normal = build_surface_detail_chain(
        nodes, links, scene, output,
        normal_img=safe_normal,
        displacement_img=safe_disp,
        loc=(-1800, -900)
    )
    normal_map_node = nodes.get('Normal Map')
    bump_node = nodes.get('Displacement Bump')

    # Directly pass the correct tangent-normal + height result to diffuse capture.
    diffuse = make_node(nodes, "ShaderNodeBsdfDiffuse", "Scene Light Capture", (-1200, -900))
    link(links, surface_normal, diffuse.inputs["Normal"])
    
    # Detail AO can be multiplied by an optional standalone AO map. Keeping these
    # separate lets packed Detail.R coexist with standard PBR AO workflows.
    ao_sep = make_node(nodes, "ShaderNodeSeparateColor", "Separate AO Channel", (-1350, 250))
    link(links, det_ao.outputs[0], ao_sep.inputs[0])
    ao_source = ao_sep.outputs[0]
    ao_tex = _map_texture_node(nodes, 'AO Map', safe_ao_map, (-1540, 180), 'AO')
    if ao_tex is not None:
        ao_bw = make_node(nodes, 'ShaderNodeRGBToBW', 'AO Luminance', (-1360, 130))
        ao_combined = make_node(nodes, 'ShaderNodeMath', 'Detail AO x AO Map', (-1180, 180))
        ao_combined.operation = 'MULTIPLY'
        link(links, ao_tex.outputs['Color'], ao_bw.inputs[0])
        link(links, ao_source, ao_combined.inputs[0])
        link(links, ao_bw.outputs[0], ao_combined.inputs[1])
        ao_source = ao_combined.outputs[0]

    # Remap AO floor so anime characters never turn pitch black from ambient occlusion
    ao_clamp = make_node(nodes, "ShaderNodeMapRange", "Clamp Cel AO Floor", (-1300, 350))
    ao_clamp.inputs["From Min"].default_value = 0.0
    ao_clamp.inputs["From Max"].default_value = 1.0
    ao_clamp.inputs["To Min"].default_value = 0.35
    ao_clamp.inputs["To Max"].default_value = 1.0
    link(links, ao_source, ao_clamp.inputs[0])

    ao_mul = make_node(nodes, "ShaderNodeMix", "Apply Global AO", (-1150, 400))
    ao_mul.data_type = 'RGBA'
    ao_mul.blend_type = 'MULTIPLY'
    link(links, base_tex.outputs[0], find_socket(ao_mul.inputs, "A", "Color1"))
    link(links, ao_clamp.outputs[0], find_socket(ao_mul.inputs, "B", "Color2"))
    find_socket(ao_mul.inputs, "Factor", "Fac").default_value = 1.0

    accent_add = make_node(nodes, "ShaderNodeMix", "Detail Accent", (-900, 400))
    accent_add.data_type = 'RGBA'
    accent_add.blend_type = 'ADD'
    link(links, det_accent.outputs[0], find_socket(accent_add.inputs, "Factor", "Fac"))
    link(links, find_socket(ao_mul.outputs, "Result", "Color"), find_socket(accent_add.inputs, "A", "Color1"))
    find_socket(accent_add.inputs, "B", "Color2").default_value = (1.0, 0.4, 0.4, 1.0) 

    pattern_type = getattr(scene, "genos_clothing_pattern_type", "NONE") if scene else "NONE"
    pattern_base = find_socket(accent_add.outputs, "Result", "Color")
    if pattern_type != "NONE":
        pattern_proc = _build_clothing_pattern_factor(nodes, links, scene, pattern_type)
        pattern_mask_bw = make_node(nodes, "ShaderNodeRGBToBW", "Pattern Mask BW", (-960, -760))
        link(links, pattern_mask.outputs[0], pattern_mask_bw.inputs[0])

        pattern_strength = make_node(nodes, "ShaderNodeMath", "Pattern Mask Strength", (-740, -760))
        pattern_strength.operation = 'MULTIPLY'
        pattern_strength.use_clamp = True
        pattern_strength.inputs[1].default_value = max(0.0, min(1.0, float(getattr(scene, "genos_pattern_strength", 0.55))))
        link(links, pattern_mask_bw.outputs[0], pattern_strength.inputs[0])

        pattern_layer_color = None
        if safe_pattern_color:
            p_uv = make_node(nodes, "ShaderNodeTexCoord", "Pattern Color UV", (-740, -1040))
            p_map = make_node(nodes, "ShaderNodeMapping", "Pattern Color Mapping", (-560, -1040))
            link(links, p_uv.outputs["UV"], p_map.inputs["Vector"])
            try:
                sc = max(0.01, float(getattr(scene, "genos_pattern_scale", 20.0)))
                p_map.inputs["Scale"].default_value = (sc, sc, 1.0)
                p_map.inputs["Rotation"].default_value = (0.0, 0.0, float(getattr(scene, "genos_pattern_rotation", 0.0)))
            except Exception:
                pass

            p_tex = make_node(nodes, "ShaderNodeTexImage", "Pattern Color Texture", (-360, -1040))
            p_tex.image = safe_pattern_color
            link(links, p_map.outputs["Vector"], p_tex.inputs["Vector"])

            tint_mul = make_node(nodes, "ShaderNodeMix", "Pattern Texture Tint", (-120, -980))
            tint_mul.data_type = 'RGBA'
            tint_mul.blend_type = 'MULTIPLY'
            find_socket(tint_mul.inputs, "Factor", "Fac").default_value = 1.0
            link(links, p_tex.outputs[0], find_socket(tint_mul.inputs, "A", "Color1"))
            find_socket(tint_mul.inputs, "B", "Color2").default_value = tuple(getattr(scene, "genos_pattern_tint", (1.0, 1.0, 1.0, 1.0)))
            pattern_layer_color = find_socket(tint_mul.outputs, "Result", "Color")

        pattern_proc_mul = make_node(nodes, "ShaderNodeMix", "Pattern Detail Multiply", (-520, -980))
        pattern_proc_mul.data_type = 'RGBA'
        pattern_proc_mul.blend_type = 'MULTIPLY'
        find_socket(pattern_proc_mul.inputs, "Factor", "Fac").default_value = 1.0
        if pattern_layer_color:
            link(links, pattern_layer_color, find_socket(pattern_proc_mul.inputs, "A", "Color1"))
        else:
            find_socket(pattern_proc_mul.inputs, "A", "Color1").default_value = tuple(getattr(scene, "genos_pattern_tint", (1.0, 1.0, 1.0, 1.0)))
        link(links, pattern_proc, find_socket(pattern_proc_mul.inputs, "B", "Color2"))

        pattern_mul_base = make_node(nodes, "ShaderNodeMix", "Pattern Over Base Multiply", (-460, 320))
        pattern_mul_base.data_type = 'RGBA'
        pattern_mul_base.blend_type = 'MULTIPLY'
        find_socket(pattern_mul_base.inputs, "Factor", "Fac").default_value = 1.0
        link(links, pattern_base, find_socket(pattern_mul_base.inputs, "A", "Color1"))
        link(links, find_socket(pattern_proc_mul.outputs, "Result", "Color"), find_socket(pattern_mul_base.inputs, "B", "Color2"))

        pattern_tint_mix = make_node(nodes, "ShaderNodeMix", "Clothing Pattern Layer", (-260, 400))
        pattern_tint_mix.data_type = 'RGBA'
        pattern_tint_mix.blend_type = 'MIX'
        link(links, pattern_strength.outputs[0], find_socket(pattern_tint_mix.inputs, "Factor", "Fac"))
        link(links, pattern_base, find_socket(pattern_tint_mix.inputs, "A", "Color1"))
        link(links, find_socket(pattern_mul_base.outputs, "Result", "Color"), find_socket(pattern_tint_mix.inputs, "B", "Color2"))
        pattern_base = find_socket(pattern_tint_mix.outputs, "Result", "Color")

    if shader_type == 'HAIR':
        pattern_base = _build_hair_tip_gradient(nodes, links, scene, pattern_base, loc=(-260, 200))

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

    sdf_tex_node = None
    if shader_type == 'FACE':
        # Advanced 2D Face / SDF Support
        sdf_tex = make_node(nodes, "ShaderNodeTexImage", "SDF Map", (-700, -1100))
        if images.get("sdf_map"):
            sdf_tex.image = images.get("sdf_map")
            set_image_colorspace(sdf_tex.image, 'Non-Color')
        sdf_tex_node = sdf_tex

        # Modern Anime Face Normal adjustment (Forward Facing Normal Override + Head tracking mapping)
        face_normal_override = make_node(nodes, "ShaderNodeNewGeometry", "Face Geo Normal", (-1500, -1100))
        norm_mix = make_node(nodes, "ShaderNodeMix", "Face Normal Mix", (-1200, -1100))
        norm_mix.data_type = 'RGBA'
        norm_mix.blend_type = 'MIX'
        find_socket(norm_mix.inputs, "Factor", "Fac").default_value = 0.45
        link(links, face_normal_override.outputs["Normal"], find_socket(norm_mix.inputs, "A", "Color1"))
        link(links, face_normal_override.outputs["Incoming"], find_socket(norm_mix.inputs, "B", "Color2"))

        norm_normalize = make_node(nodes, "ShaderNodeVectorMath", "Normalize Override", (-900, -1100))
        norm_normalize.operation = 'NORMALIZE'
        link(links, find_socket(norm_mix.outputs, "Result", "Color"), norm_normalize.inputs[0])
        link(links, norm_normalize.outputs[0], diffuse.inputs["Normal"])

    # 3-Banded Cel Shading (Nikke / Wuwa / PGR Style)
    shaded_color, light_step_1, light_step_2 = _build_3band_cel_shading(
        nodes, links, scene, shadow_add.outputs[0], pattern_base,
        sdf_tex_node=sdf_tex_node, is_face=(shader_type == 'FACE'), is_hair=(shader_type == 'HAIR'), loc=(-300, 0)
    )

    rough_tex = _map_texture_node(nodes, 'Roughness Map', safe_roughness, (-1500, -1030), 'ROUGHNESS')
    if rough_tex is not None:
        rough_bw = make_node(nodes, 'ShaderNodeRGBToBW', 'Roughness Luminance', (-1320, -1030))
        link(links, rough_tex.outputs['Color'], rough_bw.inputs[0])
        roughness_socket = rough_bw.outputs[0]
    else:
        rough_val = make_node(nodes, 'ShaderNodeValue', 'Roughness Default', (-1320, -1030))
        rough_val.outputs[0].default_value = 0.35 if shader_type == 'HAIR' else 0.50
        roughness_socket = rough_val.outputs[0]

    metal_tex = _map_texture_node(nodes, 'Metallic Map', safe_metallic, (-1500, -1140), 'METALLIC')
    if metal_tex is not None:
        metal_bw = make_node(nodes, 'ShaderNodeRGBToBW', 'Metallic Luminance', (-1320, -1140))
        link(links, metal_tex.outputs['Color'], metal_bw.inputs[0])
        metallic_socket = metal_bw.outputs[0]
    else:
        metal_val = make_node(nodes, 'ShaderNodeValue', 'Metallic Default', (-1320, -1140))
        metal_val.outputs[0].default_value = 1.0 if shader_type == 'METALLIC' else 0.0
        metallic_socket = metal_val.outputs[0]

    if shader_type == 'HAIR':
        glossy = make_node(nodes, "ShaderNodeBsdfAnisotropic", "Hair Specular Capture", (-1200, -700))
        if glossy is None:
            glossy = make_node(nodes, "ShaderNodeBsdfGlossy", "Hair Specular Capture", (-1200, -700))
        try:
            glossy.inputs["Roughness"].default_value = 0.18
            glossy.inputs["Anisotropy"].default_value = 0.85
            glossy.inputs["Rotation"].default_value = 0.25
        except: pass
    else:
        glossy = make_node(nodes, "ShaderNodeBsdfGlossy", "Specular Capture", (-1200, -700))
        try: glossy.inputs["Roughness"].default_value = 0.05
        except: pass

    if glossy.inputs.get('Roughness'):
        link(links, roughness_socket, glossy.inputs['Roughness'])
    link(links, surface_normal, glossy.inputs["Normal"])

    gs2rgb = make_node(nodes, "ShaderNodeShaderToRGB", "Glossy to RGB", (-900, -700))
    gbw_light = make_node(nodes, "ShaderNodeRGBToBW", "Glossy Intensity", (-700, -700))
    link(links, glossy.outputs[0], gs2rgb.inputs[0])
    link(links, gs2rgb.outputs[0], gbw_light.inputs[0])

    ilm_spec_bw = make_node(nodes, "ShaderNodeRGBToBW", "ILM Spec BW", (-900, -580))
    link(links, ilm_spec.outputs[0], ilm_spec_bw.inputs[0])

    # Advanced Dual-Tier Anime Specularity
    specular_color, spec_mask = _build_anime_specular_system(
        nodes, links, scene, shader_type, gbw_light.outputs[0], ilm_spec_bw.outputs[0], light_step_1, pattern_base,
        roughness_socket=roughness_socket, metallic_socket=metallic_socket, loc=(-500, -700)
    )

    if shader_type == 'HAIR':
        source_mix = make_node(nodes, 'ShaderNodeMix', 'Preserve Painted Hair', (350, 350))
        source_mix.data_type = 'RGBA'
        source_mix.blend_type = 'MIX'
        find_socket(source_mix.inputs, 'Factor').default_value = getattr(scene, 'genos_hair_source_preservation', 0.65)
        link(links, shaded_color, find_socket(source_mix.inputs, 'A'))
        link(links, base_tex.outputs['Color'], find_socket(source_mix.inputs, 'B'))
        shaded_color = find_socket(source_mix.outputs, 'Result')

    spec_add = make_node(nodes, "ShaderNodeMix", "Add Dynamic Specular", (600, 0))
    spec_add.data_type = 'RGBA'
    spec_add.blend_type = 'SCREEN'
    find_socket(spec_add.inputs, "Factor", "Fac").default_value = 1.0
    link(links, shaded_color, find_socket(spec_add.inputs, "A", "Color1"))
    link(links, specular_color, find_socket(spec_add.inputs, "B", "Color2"))

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

    # Directional Silhouette Rim Light
    ilm_rim_bw = make_node(nodes, "ShaderNodeRGBToBW", "ILM Rim BW", (1200, -800))
    link(links, ilm_rim.outputs[0], ilm_rim_bw.inputs[0])

    rim_color_out, rim_mask_out = _build_directional_rim_system(
        nodes, links, scene, bump_node.outputs["Normal"], ilm_rim_bw.outputs[0], light_step_1, loc=(1400, -800)
    )

    rim_add = make_node(nodes, "ShaderNodeMix", "Add Rim Light", (1300, 0))
    rim_add.data_type = 'RGBA'
    rim_add.blend_type = 'ADD'
    find_socket(rim_add.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(apply_lineart.outputs, "Result", "Color"), find_socket(rim_add.inputs, "A", "Color1"))
    link(links, rim_color_out, find_socket(rim_add.inputs, "B", "Color2"))

    # Advanced Emission System (Tech Glow, Overdrive & Pulse)
    hair_emit_mask_node = nodes.get('Hair Bands ILM Emission Mask') if shader_type == 'HAIR' else None
    hair_emit_color_node = nodes.get('Hair Bands ILM Emission Color') if shader_type == 'HAIR' else None
    hair_emit_mask_socket = hair_emit_mask_node.outputs[0] if hair_emit_mask_node is not None else None
    hair_emit_color_socket = find_socket(hair_emit_color_node.outputs, 'Result', 'Color') if hair_emit_color_node is not None else None
    final_emission = _build_anime_emission_system(
        nodes, links, scene, emission_source.outputs[0], ilm_emission.outputs[0], det_emit.outputs[0],
        pattern_base, light_step_1, hair_emit_mask_socket, hair_emit_color_socket, loc=(1000, -350)
    )

    final_add = make_node(nodes, "ShaderNodeMix", "Add Final Emission", (1900, 0))
    final_add.data_type = 'RGBA'
    final_add.blend_type = 'ADD'
    find_socket(final_add.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(rim_add.outputs, "Result", "Color"), find_socket(final_add.inputs, "A", "Color1"))
    link(links, final_emission, find_socket(final_add.inputs, "B", "Color2"))

    link(links, find_socket(final_add.outputs, "Result", "Color"), emission_out.inputs[0])

def build_baked_material(mat, base_img, emit_img, nmap_img, ilm_img, det_img, sdf_img=None, disp_img=None, pattern_mask_img=None, pattern_color_img=None, roughness_img=None, metallic_img=None, opacity_img=None, ao_img=None):
    mat.use_nodes = True
    mat["is_anime_toon_baked"] = True 
    if "genos_shader_type" not in mat:
        mat["genos_shader_type"] = detect_shader_type_for_material(mat)
    shader_type = mat.get("genos_shader_type", "DEFAULT")
    
    try:
        if shader_type == 'HAIR':
            mat.blend_method = 'HASHED'
            mat.shadow_method = 'CLIP'
            mat.show_transparent_back = False 
        else:
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
    strength_val.outputs[0].default_value = 1.0 
    
    link(links, strength_val.outputs[0], emission_out.inputs["Strength"])
    link(links, transparent_bsdf.outputs[0], mix_shader.inputs[1]) 
    link(links, emission_out.outputs[0], mix_shader.inputs[2])     
    link(links, mix_shader.outputs[0], output.inputs["Surface"])

    base_tex = make_node(nodes, "ShaderNodeTexImage", "BaseColor", (-1500, 400))
    if not is_valid_image(base_img):
        base_img = getattr(mat, "genos_base_color_map", None)
    if is_valid_image(base_img):
        set_image_colorspace(base_img, "sRGB")
        try: base_img.use_fake_user = True
        except Exception: pass
        base_tex.image = base_img
        try: mat.genos_base_color_map = base_img
        except Exception: pass

    try:
        mat['_genos_map_rebuild_guard'] = True
        for img, prop_name, kind in (
            (nmap_img, 'genos_normal_map', 'NORMAL'),
            (disp_img, 'genos_displacement_map', 'DISPLACEMENT'),
            (roughness_img, 'genos_roughness_map', 'ROUGHNESS'),
            (metallic_img, 'genos_metallic_map', 'METALLIC'),
            (opacity_img, 'genos_opacity_map', 'OPACITY'),
            (ao_img, 'genos_ao_map', 'AO'),
        ):
            if is_valid_image(img):
                configure_standard_map_image(img, kind)
                try: setattr(mat, prop_name, img)
                except Exception: pass
    finally:
        try: mat['_genos_map_rebuild_guard'] = False
        except Exception: pass
    
    alpha_source = base_tex.outputs['Alpha']
    opacity_tex = _map_texture_node(nodes, 'Opacity Map', opacity_img or getattr(mat, 'genos_opacity_map', None), (-1500, 520), 'OPACITY')
    if opacity_tex is not None:
        opacity_bw = make_node(nodes, 'ShaderNodeRGBToBW', 'Opacity Luminance', (-1320, 520))
        opacity_mul = make_node(nodes, 'ShaderNodeMath', 'Base Alpha x Opacity', (-1140, 520))
        opacity_mul.operation = 'MULTIPLY'
        link(links, opacity_tex.outputs['Color'], opacity_bw.inputs[0])
        link(links, base_tex.outputs['Alpha'], opacity_mul.inputs[0])
        link(links, opacity_bw.outputs[0], opacity_mul.inputs[1])
        alpha_source = opacity_mul.outputs[0]

    alpha_clip_gate = make_node(nodes, "ShaderNodeMath", "Alpha Failsafe", (2300, 200))
    alpha_clip_gate.operation = 'GREATER_THAN'
    alpha_clip_gate.inputs[1].default_value = 0.1
    link(links, alpha_source, alpha_clip_gate.inputs[0])

    if shader_type == 'HAIR':
        hair_transparency_val = make_node(nodes, "ShaderNodeValue", "Hair Transparency", (2500, 100))
        hair_transparency_val.outputs[0].default_value = getattr(bpy.context.scene, "genos_hair_transparency", 0.5) 

        # Depth-based alpha masking for eye transparency (Hair over Eyes effect)
        light_path = make_node(nodes, "ShaderNodeLightPath", "Light Path", (2300, 400))
        transparent_depth = make_node(nodes, "ShaderNodeMath", "Is Transparent Depth", (2450, 400))
        transparent_depth.operation = 'GREATER_THAN'
        link(links, light_path.outputs["Transparent Depth"], transparent_depth.inputs[0])
        transparent_depth.inputs[1].default_value = 0.0
        
        # Mix base alpha config with the transparency blend whenever depth check hits
        depth_mix = make_node(nodes, "ShaderNodeMix", "Depth Mix Alpha", (2600, 300))
        depth_mix.data_type = 'FLOAT'
        depth_mix.blend_type = 'MIX'
        link(links, transparent_depth.outputs[0], depth_mix.inputs["Factor"])
        link(links, hair_transparency_val.outputs[0], depth_mix.inputs["B"])
        depth_mix.inputs["A"].default_value = 1.0 # 1.0 alpha normal case

        # Multiplied overall hair alpha factor
        hair_alpha_factor = make_node(nodes, "ShaderNodeMath", "Hair Alpha Factor", (2700, 150))
        hair_alpha_factor.operation = 'MULTIPLY'
        link(links, alpha_clip_gate.outputs[0], hair_alpha_factor.inputs[0])
        link(links, depth_mix.outputs["Result"], hair_alpha_factor.inputs[1])
        link(links, hair_alpha_factor.outputs[0], mix_shader.inputs[0])
    else:
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

    pattern_tex = make_node(nodes, "ShaderNodeTexImage", "Pattern Mask", (-1500, -280))
    if pattern_mask_img:
        set_image_colorspace(pattern_mask_img, MASK_COLORSPACE)
        pattern_tex.image = pattern_mask_img

    surface_normal = build_surface_detail_chain(
        nodes, links, bpy.context.scene, output,
        normal_img=nmap_img or getattr(mat, 'genos_normal_map', None),
        displacement_img=disp_img or getattr(mat, 'genos_displacement_map', None),
        loc=(-1800, -900)
    )
    normal_map_node = nodes.get('Normal Map')
    bump_node = nodes.get('Displacement Bump')

    ao_source = det_sep.outputs[0]
    ao_tex = _map_texture_node(nodes, 'AO Map', ao_img or getattr(mat, 'genos_ao_map', None), (-1540, 180), 'AO')
    if ao_tex is not None:
        ao_bw = make_node(nodes, 'ShaderNodeRGBToBW', 'AO Luminance', (-1360, 130))
        ao_combined = make_node(nodes, 'ShaderNodeMath', 'Detail AO x AO Map', (-1180, 180))
        ao_combined.operation = 'MULTIPLY'
        link(links, ao_tex.outputs['Color'], ao_bw.inputs[0])
        link(links, ao_source, ao_combined.inputs[0]); link(links, ao_bw.outputs[0], ao_combined.inputs[1])
        ao_source = ao_combined.outputs[0]

    # Remap AO floor so anime characters never turn pitch black from ambient occlusion
    ao_clamp = make_node(nodes, "ShaderNodeMapRange", "Clamp Cel AO Floor", (-1300, 350))
    ao_clamp.inputs["From Min"].default_value = 0.0
    ao_clamp.inputs["From Max"].default_value = 1.0
    ao_clamp.inputs["To Min"].default_value = 0.35
    ao_clamp.inputs["To Max"].default_value = 1.0
    link(links, ao_source, ao_clamp.inputs[0])

    ao_mul = make_node(nodes, "ShaderNodeMix", "Apply Global AO", (-1150, 400))
    ao_mul.data_type = 'RGBA'
    ao_mul.blend_type = 'MULTIPLY'
    link(links, base_tex.outputs[0], find_socket(ao_mul.inputs, "A", "Color1"))
    link(links, ao_clamp.outputs[0], find_socket(ao_mul.inputs, "B", "Color2"))
    find_socket(ao_mul.inputs, "Factor", "Fac").default_value = 1.0

    accent_add = make_node(nodes, "ShaderNodeMix", "Detail Accent", (-900, 400))
    accent_add.data_type = 'RGBA'
    accent_add.blend_type = 'ADD'
    link(links, det_sep.outputs[2], find_socket(accent_add.inputs, "Factor", "Fac")) 
    link(links, find_socket(ao_mul.outputs, "Result", "Color"), find_socket(accent_add.inputs, "A", "Color1"))
    find_socket(accent_add.inputs, "B", "Color2").default_value = (1.0, 0.4, 0.4, 1.0) 

    pattern_type = getattr(scene, "genos_clothing_pattern_type", "NONE") if scene else "NONE"
    pattern_base = find_socket(accent_add.outputs, "Result", "Color")
    if pattern_type != "NONE":
        pattern_proc = _build_clothing_pattern_factor(nodes, links, scene, pattern_type)
        pattern_mask_bw = make_node(nodes, "ShaderNodeRGBToBW", "Pattern Mask BW", (-960, -760))
        link(links, pattern_tex.outputs[0], pattern_mask_bw.inputs[0])

        pattern_strength = make_node(nodes, "ShaderNodeMath", "Pattern Mask Strength", (-740, -760))
        pattern_strength.operation = 'MULTIPLY'
        pattern_strength.use_clamp = True
        pattern_strength.inputs[1].default_value = max(0.0, min(1.0, float(getattr(scene, "genos_pattern_strength", 0.55))))
        link(links, pattern_mask_bw.outputs[0], pattern_strength.inputs[0])

        pattern_layer_color = None
        pattern_color_img = pattern_color_img if pattern_color_img is not None else getattr(mat, "genos_pattern_color_map", None)
        if pattern_color_img:
            set_image_colorspace(pattern_color_img, "sRGB")
            p_uv = make_node(nodes, "ShaderNodeTexCoord", "Pattern Color UV", (-740, -1040))
            p_map = make_node(nodes, "ShaderNodeMapping", "Pattern Color Mapping", (-560, -1040))
            link(links, p_uv.outputs["UV"], p_map.inputs["Vector"])
            try:
                sc = max(0.01, float(getattr(scene, "genos_pattern_scale", 20.0)))
                p_map.inputs["Scale"].default_value = (sc, sc, 1.0)
                p_map.inputs["Rotation"].default_value = (0.0, 0.0, float(getattr(scene, "genos_pattern_rotation", 0.0)))
            except Exception:
                pass

            p_tex = make_node(nodes, "ShaderNodeTexImage", "Pattern Color Texture", (-360, -1040))
            p_tex.image = pattern_color_img
            link(links, p_map.outputs["Vector"], p_tex.inputs["Vector"])

            tint_mul = make_node(nodes, "ShaderNodeMix", "Pattern Texture Tint", (-120, -980))
            tint_mul.data_type = 'RGBA'
            tint_mul.blend_type = 'MULTIPLY'
            find_socket(tint_mul.inputs, "Factor", "Fac").default_value = 1.0
            link(links, p_tex.outputs[0], find_socket(tint_mul.inputs, "A", "Color1"))
            find_socket(tint_mul.inputs, "B", "Color2").default_value = tuple(getattr(scene, "genos_pattern_tint", (1.0, 1.0, 1.0, 1.0)))
            pattern_layer_color = find_socket(tint_mul.outputs, "Result", "Color")

        pattern_proc_mul = make_node(nodes, "ShaderNodeMix", "Pattern Detail Multiply", (-520, -980))
        pattern_proc_mul.data_type = 'RGBA'
        pattern_proc_mul.blend_type = 'MULTIPLY'
        find_socket(pattern_proc_mul.inputs, "Factor", "Fac").default_value = 1.0
        if pattern_layer_color:
            link(links, pattern_layer_color, find_socket(pattern_proc_mul.inputs, "A", "Color1"))
        else:
            find_socket(pattern_proc_mul.inputs, "A", "Color1").default_value = tuple(getattr(scene, "genos_pattern_tint", (1.0, 1.0, 1.0, 1.0)))
        link(links, pattern_proc, find_socket(pattern_proc_mul.inputs, "B", "Color2"))

        pattern_mul_base = make_node(nodes, "ShaderNodeMix", "Pattern Over Base Multiply", (-460, 320))
        pattern_mul_base.data_type = 'RGBA'
        pattern_mul_base.blend_type = 'MULTIPLY'
        find_socket(pattern_mul_base.inputs, "Factor", "Fac").default_value = 1.0
        link(links, pattern_base, find_socket(pattern_mul_base.inputs, "A", "Color1"))
        link(links, find_socket(pattern_proc_mul.outputs, "Result", "Color"), find_socket(pattern_mul_base.inputs, "B", "Color2"))

        pattern_tint_mix = make_node(nodes, "ShaderNodeMix", "Clothing Pattern Layer", (-260, 400))
        pattern_tint_mix.data_type = 'RGBA'
        pattern_tint_mix.blend_type = 'MIX'
        link(links, pattern_strength.outputs[0], find_socket(pattern_tint_mix.inputs, "Factor", "Fac"))
        link(links, pattern_base, find_socket(pattern_tint_mix.inputs, "A", "Color1"))
        link(links, find_socket(pattern_mul_base.outputs, "Result", "Color"), find_socket(pattern_tint_mix.inputs, "B", "Color2"))
        pattern_base = find_socket(pattern_tint_mix.outputs, "Result", "Color")

    if shader_type == 'HAIR':
        pattern_base = _build_hair_tip_gradient(nodes, links, scene, pattern_base, loc=(-260, 200))

    diffuse = make_node(nodes, "ShaderNodeBsdfDiffuse", "Scene Light Capture", (-1200, -900))
    link(links, surface_normal, diffuse.inputs["Normal"])
    
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

    sdf_tex_node = None
    if shader_type == 'FACE':
        sdf_tex_node = make_node(nodes, "ShaderNodeTexImage", "SDF Map", (-700, -1100))
        if sdf_img:
            sdf_tex_node.image = sdf_img
            set_image_colorspace(sdf_tex_node.image, 'Non-Color')

        face_normal_override = make_node(nodes, "ShaderNodeNewGeometry", "Face Geo Normal", (-1500, -1100))
        norm_mix = make_node(nodes, "ShaderNodeMix", "Face Normal Mix", (-1200, -1100))
        norm_mix.data_type = 'RGBA'
        norm_mix.blend_type = 'MIX'
        find_socket(norm_mix.inputs, "Factor", "Fac").default_value = 0.45
        link(links, face_normal_override.outputs["Normal"], find_socket(norm_mix.inputs, "A", "Color1"))
        link(links, face_normal_override.outputs["Incoming"], find_socket(norm_mix.inputs, "B", "Color2"))

        norm_normalize = make_node(nodes, "ShaderNodeVectorMath", "Normalize Override", (-900, -1100))
        norm_normalize.operation = 'NORMALIZE'
        link(links, find_socket(norm_mix.outputs, "Result", "Color"), norm_normalize.inputs[0])
        link(links, norm_normalize.outputs[0], diffuse.inputs["Normal"])

    # 3-Banded Cel Shading (Nikke / Wuwa / PGR Style)
    shaded_color, light_step_1, light_step_2 = _build_3band_cel_shading(
        nodes, links, scene, shadow_add.outputs[0], pattern_base,
        sdf_tex_node=sdf_tex_node, is_face=(shader_type == 'FACE'), is_hair=(shader_type == 'HAIR'), loc=(-300, 0)
    )

    rough_tex = _map_texture_node(nodes, 'Roughness Map', roughness_img or getattr(mat, 'genos_roughness_map', None), (-1500, -1030), 'ROUGHNESS')
    if rough_tex is not None:
        rough_bw = make_node(nodes, 'ShaderNodeRGBToBW', 'Roughness Luminance', (-1320, -1030))
        link(links, rough_tex.outputs['Color'], rough_bw.inputs[0])
        roughness_socket = rough_bw.outputs[0]
    else:
        rough_val = make_node(nodes, 'ShaderNodeValue', 'Roughness Default', (-1320, -1030))
        rough_val.outputs[0].default_value = 0.35 if shader_type == 'HAIR' else 0.50
        roughness_socket = rough_val.outputs[0]

    metal_tex = _map_texture_node(nodes, 'Metallic Map', metallic_img or getattr(mat, 'genos_metallic_map', None), (-1500, -1140), 'METALLIC')
    if metal_tex is not None:
        metal_bw = make_node(nodes, 'ShaderNodeRGBToBW', 'Metallic Luminance', (-1320, -1140))
        link(links, metal_tex.outputs['Color'], metal_bw.inputs[0])
        metallic_socket = metal_bw.outputs[0]
    else:
        metal_val = make_node(nodes, 'ShaderNodeValue', 'Metallic Default', (-1320, -1140))
        metal_val.outputs[0].default_value = 1.0 if shader_type == 'METALLIC' else 0.0
        metallic_socket = metal_val.outputs[0]

    if shader_type == 'HAIR':
        glossy = make_node(nodes, "ShaderNodeBsdfAnisotropic", "Hair Specular", (-1200, -700))
        if glossy is None:
            glossy = make_node(nodes, "ShaderNodeBsdfGlossy", "Hair Specular", (-1200, -700))
        try:
            glossy.inputs["Roughness"].default_value = 0.18
            glossy.inputs["Anisotropy"].default_value = 0.85
            glossy.inputs["Rotation"].default_value = 0.25
        except: pass
    else:
        glossy = make_node(nodes, "ShaderNodeBsdfGlossy", "Specular Capture", (-1200, -700))
        try: glossy.inputs["Roughness"].default_value = 0.05
        except: pass

    if glossy.inputs.get('Roughness'):
        link(links, roughness_socket, glossy.inputs['Roughness'])
    link(links, surface_normal, glossy.inputs["Normal"])

    gs2rgb = make_node(nodes, "ShaderNodeShaderToRGB", "Glossy to RGB", (-900, -700))
    gbw_light = make_node(nodes, "ShaderNodeRGBToBW", "Glossy Intensity", (-700, -700))
    link(links, glossy.outputs[0], gs2rgb.inputs[0])
    link(links, gs2rgb.outputs[0], gbw_light.inputs[0])

    # Advanced Dual-Tier Anime Specularity with packed ILM.B (specular mask)
    specular_color, spec_mask = _build_anime_specular_system(
        nodes, links, scene, shader_type, gbw_light.outputs[0], ilm_sep.outputs[2], light_step_1, pattern_base,
        roughness_socket=roughness_socket, metallic_socket=metallic_socket, loc=(-500, -700)
    )

    if shader_type == 'HAIR':
        source_mix = make_node(nodes, 'ShaderNodeMix', 'Preserve Painted Hair', (350, 350))
        source_mix.data_type = 'RGBA'
        source_mix.blend_type = 'MIX'
        find_socket(source_mix.inputs, 'Factor').default_value = getattr(scene, 'genos_hair_source_preservation', 0.65)
        link(links, shaded_color, find_socket(source_mix.inputs, 'A'))
        link(links, base_tex.outputs['Color'], find_socket(source_mix.inputs, 'B'))
        shaded_color = find_socket(source_mix.outputs, 'Result')

    spec_add = make_node(nodes, "ShaderNodeMix", "Add Dynamic Specular", (600, 0))
    spec_add.data_type = 'RGBA'
    spec_add.blend_type = 'SCREEN'
    find_socket(spec_add.inputs, "Factor", "Fac").default_value = 1.0
    link(links, shaded_color, find_socket(spec_add.inputs, "A", "Color1"))
    link(links, specular_color, find_socket(spec_add.inputs, "B", "Color2"))

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

    # Directional Silhouette Rim Light with packed ILM.A (Alpha socket of ILM image node)
    rim_color_out, rim_mask_out = _build_directional_rim_system(
        nodes, links, scene, surface_normal, ilm_tex.outputs[1], light_step_1, loc=(1400, -800)
    )

    rim_add = make_node(nodes, "ShaderNodeMix", "Add Rim Light", (1300, 0))
    rim_add.data_type = 'RGBA'
    rim_add.blend_type = 'ADD'
    find_socket(rim_add.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(apply_lineart.outputs, "Result", "Color"), find_socket(rim_add.inputs, "A", "Color1"))
    link(links, rim_color_out, find_socket(rim_add.inputs, "B", "Color2"))

    # Advanced Emission System (Tech Glow, Overdrive & Pulse) with packed ILM.G + Detail.A
    hair_emit_mask_node = nodes.get('Hair Bands ILM Emission Mask') if shader_type == 'HAIR' else None
    hair_emit_color_node = nodes.get('Hair Bands ILM Emission Color') if shader_type == 'HAIR' else None
    hair_emit_mask_socket = hair_emit_mask_node.outputs[0] if hair_emit_mask_node is not None else None
    hair_emit_color_socket = find_socket(hair_emit_color_node.outputs, 'Result', 'Color') if hair_emit_color_node is not None else None
    final_emission = _build_anime_emission_system(
        nodes, links, scene, emission_source.outputs[0], ilm_sep.outputs[1], det_tex.outputs[1],
        pattern_base, light_step_1, hair_emit_mask_socket, hair_emit_color_socket, loc=(1000, -350)
    )

    final_add = make_node(nodes, "ShaderNodeMix", "Add Final Emission", (1900, 0))
    final_add.data_type = 'RGBA'
    final_add.blend_type = 'ADD'
    find_socket(final_add.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(rim_add.outputs, "Result", "Color"), find_socket(final_add.inputs, "A", "Color1"))
    link(links, final_emission, find_socket(final_add.inputs, "B", "Color2"))

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
        "DETAIL_EMISSION": "Detail_Emission",
        "PATTERN_MASK": "Pattern Mask"
    }
    name = node_map.get(target)
    if name:
        node = mat.node_tree.nodes.get(name)
        return node.image if node else None
    return None

def _apply_lineart_preset(scene):
    preset = getattr(scene, "genos_lineart_preset", "CUSTOM")
    if preset == 'ULTRA_FINE':
        scene.genos_lineart_radius = 1e-06
        scene.genos_lineart_samples = 32
        scene.genos_lineart_edge_min = 0.002
        scene.genos_lineart_edge_max = 0.045
        scene.genos_lineart_gamma = 2.4
        scene.genos_lineart_smooth = True
    elif preset == 'BALANCED':
        scene.genos_lineart_radius = 0.01
        scene.genos_lineart_samples = 16
        scene.genos_lineart_edge_min = 0.01
        scene.genos_lineart_edge_max = 0.15
        scene.genos_lineart_gamma = 1.0
        scene.genos_lineart_smooth = True
    elif preset == 'CRISP_INK':
        scene.genos_lineart_radius = 0.005
        scene.genos_lineart_samples = 24
        scene.genos_lineart_edge_min = 0.006
        scene.genos_lineart_edge_max = 0.08
        scene.genos_lineart_gamma = 3.0
        scene.genos_lineart_smooth = False
    elif preset == 'SOFT_ANIME':
        scene.genos_lineart_radius = 0.02
        scene.genos_lineart_samples = 12
        scene.genos_lineart_edge_min = 0.02
        scene.genos_lineart_edge_max = 0.22
        scene.genos_lineart_gamma = 0.8
        scene.genos_lineart_smooth = True

def _lineart_preset_update(self, context):
    try:
        _apply_lineart_preset(self)
    except Exception:
        pass

def _build_clothing_pattern_factor(nodes, links, scene, pattern_type):
    tex_coord = make_node(nodes, "ShaderNodeTexCoord", "Pattern UV", (-1900, -1120))
    mapping = make_node(nodes, "ShaderNodeMapping", "Pattern Mapping", (-1700, -1120))
    link(links, tex_coord.outputs["UV"], mapping.inputs["Vector"])

    scale = max(0.01, float(getattr(scene, "genos_pattern_scale", 20.0)))
    rot = float(getattr(scene, "genos_pattern_rotation", 0.0))
    try:
        mapping.inputs["Scale"].default_value = (scale, scale, 1.0)
    except Exception:
        pass
    try:
        mapping.inputs["Rotation"].default_value = (0.0, 0.0, rot)
    except Exception:
        pass

    if pattern_type == 'PANTYHOSE':
        voro = make_node(nodes, "ShaderNodeTexVoronoi", "Pattern Pantyhose Voronoi", (-1480, -1060))
        voro.feature = 'F1'
        voro.inputs["Scale"].default_value = 55.0
        link(links, mapping.outputs["Vector"], voro.inputs["Vector"])

        edge = make_node(nodes, "ShaderNodeMapRange", "Pattern Pantyhose Edge", (-1240, -1060))
        edge.interpolation_type = 'SMOOTHSTEP'
        edge.inputs["From Min"].default_value = 0.0
        edge.inputs["From Max"].default_value = 0.035
        edge.inputs["To Min"].default_value = 1.0
        edge.inputs["To Max"].default_value = 0.0
        link(links, voro.outputs["Distance"], edge.inputs["Value"])
        return edge.outputs["Result"]

    if pattern_type == 'STRIPES':
        wave = make_node(nodes, "ShaderNodeTexWave", "Pattern Stripes", (-1480, -1060))
        wave.wave_type = 'BANDS'
        wave.bands_direction = 'Y'
        wave.inputs["Scale"].default_value = 24.0
        wave.inputs["Distortion"].default_value = 0.45
        link(links, mapping.outputs["Vector"], wave.inputs["Vector"])

        stripe = make_node(nodes, "ShaderNodeValToRGB", "Pattern Stripe Ramp", (-1240, -1060))
        stripe.color_ramp.elements[0].position = 0.46
        stripe.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        stripe.color_ramp.elements[1].position = 0.54
        stripe.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        link(links, wave.outputs["Color"], stripe.inputs["Fac"])
        return stripe.outputs["Color"]

    if pattern_type == 'RIPPED':
        noise = make_node(nodes, "ShaderNodeTexNoise", "Pattern Ripped Noise", (-1480, -1060))
        noise.inputs["Scale"].default_value = 10.0
        noise.inputs["Detail"].default_value = 14.0
        noise.inputs["Roughness"].default_value = 0.82
        link(links, mapping.outputs["Vector"], noise.inputs["Vector"])

        tear = make_node(nodes, "ShaderNodeValToRGB", "Pattern Ripped Ramp", (-1240, -1060))
        tear.color_ramp.elements[0].position = 0.42
        tear.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        tear.color_ramp.elements[1].position = 0.56
        tear.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        link(links, noise.outputs["Fac"], tear.inputs["Fac"])
        return tear.outputs["Color"]

    if pattern_type == 'BODYSUIT_HEX':
        voro = make_node(nodes, "ShaderNodeTexVoronoi", "Pattern Hex", (-1480, -1060))
        voro.feature = 'SMOOTH_F1'
        voro.inputs["Scale"].default_value = 26.0
        link(links, mapping.outputs["Vector"], voro.inputs["Vector"])

        hexr = make_node(nodes, "ShaderNodeMapRange", "Pattern Hex Edge", (-1240, -1060))
        hexr.interpolation_type = 'SMOOTHSTEP'
        hexr.inputs["From Min"].default_value = 0.0
        hexr.inputs["From Max"].default_value = 0.2
        hexr.inputs["To Min"].default_value = 1.0
        hexr.inputs["To Max"].default_value = 0.0
        link(links, voro.outputs["Distance"], hexr.inputs["Value"])
        return hexr.outputs["Result"]

    if pattern_type == 'DOTS':
        voro = make_node(nodes, "ShaderNodeTexVoronoi", "Pattern Dots", (-1480, -1060))
        voro.feature = 'F1'
        voro.inputs["Scale"].default_value = 36.0
        link(links, mapping.outputs["Vector"], voro.inputs["Vector"])

        dots = make_node(nodes, "ShaderNodeMapRange", "Pattern Dot Mask", (-1240, -1060))
        dots.interpolation_type = 'SMOOTHSTEP'
        dots.inputs["From Min"].default_value = 0.0
        dots.inputs["From Max"].default_value = 0.08
        dots.inputs["To Min"].default_value = 1.0
        dots.inputs["To Max"].default_value = 0.0
        link(links, voro.outputs["Distance"], dots.inputs["Value"])
        return dots.outputs["Result"]

    if pattern_type == 'COTTON':
        noise = make_node(nodes, "ShaderNodeTexNoise", "Pattern Cotton Noise", (-1480, -1060))
        noise.inputs["Scale"].default_value = 85.0
        noise.inputs["Detail"].default_value = 12.0
        noise.inputs["Roughness"].default_value = 0.42
        link(links, mapping.outputs["Vector"], noise.inputs["Vector"])

        cotton = make_node(nodes, "ShaderNodeMapRange", "Pattern Cotton Fiber", (-1240, -1060))
        cotton.interpolation_type = 'SMOOTHSTEP'
        cotton.inputs["From Min"].default_value = 0.42
        cotton.inputs["From Max"].default_value = 0.72
        cotton.inputs["To Min"].default_value = 0.0
        cotton.inputs["To Max"].default_value = 1.0
        link(links, noise.outputs["Fac"], cotton.inputs["Value"])
        return cotton.outputs["Result"]

    if pattern_type == 'LEATHER':
        musgrave = make_node(nodes, "ShaderNodeTexMusgrave", "Pattern Leather Grain", (-1480, -1060))
        musgrave.musgrave_type = 'RIDGED_MULTIFRACTAL'
        musgrave.inputs["Scale"].default_value = 38.0
        musgrave.inputs["Detail"].default_value = 8.0
        musgrave.inputs["Dimension"].default_value = 0.55
        musgrave.inputs["Lacunarity"].default_value = 2.1
        link(links, mapping.outputs["Vector"], musgrave.inputs["Vector"])

        leather = make_node(nodes, "ShaderNodeMapRange", "Pattern Leather Pores", (-1240, -1060))
        leather.interpolation_type = 'SMOOTHSTEP'
        leather.inputs["From Min"].default_value = 0.30
        leather.inputs["From Max"].default_value = 0.68
        leather.inputs["To Min"].default_value = 0.0
        leather.inputs["To Max"].default_value = 1.0
        link(links, musgrave.outputs["Fac"], leather.inputs["Value"])
        return leather.outputs["Result"]

    val = make_node(nodes, "ShaderNodeValue", "Pattern Disabled", (-1240, -1060))
    val.outputs[0].default_value = 0.0
    return val.outputs[0]

# -------------------------------------------------------------------
# Advanced Anime Shading Core (Nikke / Wuwa / PGR Pipeline)
# -------------------------------------------------------------------

def configure_smooth_step(node, from_min, from_max, to_min=0.0, to_max=1.0):
    try:
        node.interpolation_type = 'SMOOTHSTEP'
    except Exception:
        pass
    try:
        node.clamp = True
    except Exception:
        pass
    try:
        node.inputs["From Min"].default_value = from_min
        node.inputs["From Max"].default_value = from_max
        node.inputs["To Min"].default_value = to_min
        node.inputs["To Max"].default_value = to_max
    except Exception:
        try:
            node.inputs[1].default_value = from_min
            node.inputs[2].default_value = from_max
            node.inputs[3].default_value = to_min
            node.inputs[4].default_value = to_max
        except Exception:
            pass

def _build_hair_tip_gradient(nodes, links, scene, base_color_socket, loc=(-600, 400)):
    """Creates an authentic anime tip ombre gradient on hair tufts.
    
    Supports:
    - 3D Geometry Color Attribute 'Hair_Ombre' (auto-detected from clumps and boundary tip edges).
    - UV V-coordinate fallback if 'Hair_Ombre' attribute is not present.
    - Configurable Spread / Range, Falloff Power curve, Blend Mode, and Tint Color.
    """
    tip_str = float(getattr(scene, "genos_hair_tip_strength", 0.85))
    spread = float(getattr(scene, "genos_hair_ombre_range", 0.35))
    power = float(getattr(scene, "genos_hair_ombre_power", 1.5))
    blend = getattr(scene, "genos_hair_ombre_blend", "MIX")
    tint_color = tuple(getattr(scene, "genos_hair_tip_tint", (0.92, 0.32, 0.52, 1.0)))
    coord_mode = getattr(scene, "genos_hair_ombre_coord_mode", "MESH")

    attr_node = make_node(nodes, "ShaderNodeAttribute", "Hair Ombre Attribute", (loc[0] - 700, loc[1]))
    attr_node.attribute_name = "Hair_Ombre"

    uv_node = make_node(nodes, "ShaderNodeTexCoord", "Hair Tip UV", (loc[0] - 700, loc[1] - 250))
    sep_uv = make_node(nodes, "ShaderNodeSeparateXYZ", "Hair Tip Sep UV", (loc[0] - 520, loc[1] - 250))
    link(links, uv_node.outputs["UV"], sep_uv.inputs[0])
    inv_y = make_node(nodes, "ShaderNodeMath", "Hair Tip Invert UV Y", (loc[0] - 360, loc[1] - 250))
    inv_y.operation = 'SUBTRACT'
    inv_y.inputs[0].default_value = 1.0
    link(links, sep_uv.outputs["Y"], inv_y.inputs[1])

    # Coordinate selection & spread mapping
    from_min = max(0.0, 1.0 - spread)
    map_range = make_node(nodes, "ShaderNodeMapRange", "Hair Ombre Map Range", (loc[0] - 180, loc[1]))
    map_range.clamp = True
    map_range.inputs["From Min"].default_value = from_min
    map_range.inputs["From Max"].default_value = 1.0
    map_range.inputs["To Min"].default_value = 0.0
    map_range.inputs["To Max"].default_value = 1.0

    if coord_mode == "UV":
        link(links, inv_y.outputs[0], map_range.inputs[0])
    elif coord_mode == "OBJECT_Z":
        obj_sep = make_node(nodes, "ShaderNodeSeparateXYZ", "Hair Tip Obj Sep", (loc[0] - 520, loc[1] - 450))
        link(links, uv_node.outputs["Object"], obj_sep.inputs[0])
        link(links, obj_sep.outputs["Z"], map_range.inputs[0])
    else:
        # Default MESH: use Hair_Ombre attribute, or combine with UV Y fallback
        combine_fac = make_node(nodes, "ShaderNodeMath", "Hair Tip Factor Combine", (loc[0] - 360, loc[1]))
        combine_fac.operation = 'MAXIMUM'
        link(links, attr_node.outputs["Factor"], combine_fac.inputs[0])
        link(links, inv_y.outputs[0], combine_fac.inputs[1])
        link(links, combine_fac.outputs[0], map_range.inputs[0])

    power_node = make_node(nodes, "ShaderNodeMath", "Hair Ombre Power", (loc[0] + 40, loc[1]))
    power_node.operation = 'POWER'
    power_node.use_clamp = True
    link(links, map_range.outputs[0], power_node.inputs[0])
    power_node.inputs[1].default_value = power

    gain_node = make_node(nodes, "ShaderNodeMath", "Hair Ombre Master Gain", (loc[0] + 240, loc[1]))
    gain_node.operation = 'MULTIPLY'
    gain_node.use_clamp = True
    link(links, power_node.outputs[0], gain_node.inputs[0])
    gain_node.inputs[1].default_value = min(1.0, max(0.0, tip_str))

    tip_mix = make_node(nodes, "ShaderNodeMix", "Hair Tip Color Mix", (loc[0] + 440, loc[1]))
    tip_mix.data_type = 'RGBA'
    tip_mix.blend_type = blend
    link(links, gain_node.outputs[0], find_socket(tip_mix.inputs, "Factor", "Fac"))
    link(links, base_color_socket, find_socket(tip_mix.inputs, "A", "Color1"))
    find_socket(tip_mix.inputs, "B", "Color2").default_value = tint_color

    return find_socket(tip_mix.outputs, "Result", "Color")

def _build_3band_cel_shading(nodes, links, scene, light_intensity_socket, base_color_socket, sdf_tex_node=None, is_face=False, is_hair=False, loc=(-300, 0)):
    """
    3-Banded Cel Shading (Nikke / Wuwa / PGR Style):
    - Band 1 (Lit -> 1st Shadow): Midtone warm transition
    - Band 2 (1st Shadow -> 2nd Shadow): Deep core occlusion
    - SSS Terminator Edge Fringe: Vibrant warm subsurface edge bleed
    """
    band1_thresh = float(getattr(scene, "genos_shadow_band1_thresh", 0.52))
    band2_thresh = float(getattr(scene, "genos_shadow_band2_thresh", 0.28))
    feather = max(0.001, float(getattr(scene, "genos_shadow_feather", 0.035)))
    
    has_valid_sdf = (
        is_face and
        sdf_tex_node is not None and
        getattr(sdf_tex_node, "image", None) is not None and
        getattr(sdf_tex_node.image, "has_data", False)
    )

    # Hair uses the unlimited dynamic toon-band stack. Face/default shaders keep
    # the original fixed/SDF path for backward compatibility.
    if is_hair and not has_valid_sdf:
        return _build_dynamic_hair_toon_shading(
            nodes, links, scene, light_intensity_socket, base_color_socket, loc=loc
        )

    if has_valid_sdf:
        sdf_min = make_node(nodes, "ShaderNodeMath", "SDF Min Edge", (loc[0] - 300, loc[1] - 800))
        sdf_min.operation = 'SUBTRACT'
        link(links, sdf_tex_node.outputs[0], sdf_min.inputs[0])
        sdf_min.inputs[1].default_value = feather
        
        sdf_max = make_node(nodes, "ShaderNodeMath", "SDF Max Edge", (loc[0] - 300, loc[1] - 900))
        sdf_max.operation = 'ADD'
        link(links, sdf_tex_node.outputs[0], sdf_max.inputs[0])
        sdf_max.inputs[1].default_value = feather
        
        shadow_step_1 = make_node(nodes, "ShaderNodeMapRange", "SDF Shadow Band 1", (loc[0] - 100, loc[1] - 800))
        shadow_step_1.interpolation_type = 'SMOOTHSTEP'
        link(links, light_intensity_socket, shadow_step_1.inputs[0])
        link(links, sdf_min.outputs[0], shadow_step_1.inputs[1])
        link(links, sdf_max.outputs[0], shadow_step_1.inputs[2])
        
        sdf_core_min = make_node(nodes, "ShaderNodeMath", "SDF Core Min Edge", (loc[0] - 300, loc[1] - 1000))
        sdf_core_min.operation = 'SUBTRACT'
        link(links, sdf_min.outputs[0], sdf_core_min.inputs[0])
        sdf_core_min.inputs[1].default_value = 0.24
        
        sdf_core_max = make_node(nodes, "ShaderNodeMath", "SDF Core Max Edge", (loc[0] - 300, loc[1] - 1100))
        sdf_core_max.operation = 'SUBTRACT'
        link(links, sdf_max.outputs[0], sdf_core_max.inputs[0])
        sdf_core_max.inputs[1].default_value = 0.24
        
        shadow_step_2 = make_node(nodes, "ShaderNodeMapRange", "SDF Shadow Band 2", (loc[0] - 100, loc[1] - 1000))
        shadow_step_2.interpolation_type = 'SMOOTHSTEP'
        link(links, light_intensity_socket, shadow_step_2.inputs[0])
        link(links, sdf_core_min.outputs[0], shadow_step_2.inputs[1])
        link(links, sdf_core_max.outputs[0], shadow_step_2.inputs[2])
    else:
        if is_face:
            # Flattering soft anime face cel shading fallback (keeps face mostly lit & smooth)
            f_band1 = 0.18
            f_band2 = 0.06
            f_feather = 0.05
            shadow_step_1 = make_node(nodes, "ShaderNodeMapRange", "Face Shadow Step Band 1", (loc[0] - 200, loc[1] - 800))
            configure_smooth_step(shadow_step_1, f_band1 - f_feather, f_band1 + f_feather)
            link(links, light_intensity_socket, shadow_step_1.inputs[0])
            
            shadow_step_2 = make_node(nodes, "ShaderNodeMapRange", "Face Shadow Step Band 2", (loc[0] - 200, loc[1] - 950))
            configure_smooth_step(shadow_step_2, f_band2 - f_feather, f_band2 + f_feather)
            link(links, light_intensity_socket, shadow_step_2.inputs[0])
        elif is_hair:
            # Hair-specific flattering anime cel shading:
            # Prevents dark muddy shadow on hair cards, keeping radiant coral red lit areas
            h_band2, h_band1, h_feather = _hair_band_edges(scene)
            
            shadow_step_1 = make_node(nodes, "ShaderNodeMapRange", "Hair Shadow Step Band 1", (loc[0] - 200, loc[1] - 800))
            configure_smooth_step(shadow_step_1, h_band1 - h_feather, h_band1 + h_feather)
            link(links, light_intensity_socket, shadow_step_1.inputs[0])
            
            shadow_step_2 = make_node(nodes, "ShaderNodeMapRange", "Hair Shadow Step Band 2", (loc[0] - 200, loc[1] - 950))
            configure_smooth_step(shadow_step_2, h_band2 - h_feather, h_band2 + h_feather)
            link(links, light_intensity_socket, shadow_step_2.inputs[0])
        else:
            shadow_step_1 = make_node(nodes, "ShaderNodeMapRange", "Shadow Step Band 1", (loc[0] - 200, loc[1] - 800))
            configure_smooth_step(shadow_step_1, band1_thresh - feather, band1_thresh + feather)
            link(links, light_intensity_socket, shadow_step_1.inputs[0])
            
            shadow_step_2 = make_node(nodes, "ShaderNodeMapRange", "Shadow Step Band 2", (loc[0] - 200, loc[1] - 950))
            configure_smooth_step(shadow_step_2, band2_thresh - feather, band2_thresh + feather)
            link(links, light_intensity_socket, shadow_step_2.inputs[0])

    if is_hair:
        shadow_col_1 = tuple(getattr(scene, "genos_hair_shadow_color_1", (0.85, 0.48, 0.58, 1.0)))
        shadow_col_2 = tuple(getattr(scene, "genos_hair_shadow_color_2", (0.58, 0.22, 0.32, 1.0)))
    else:
        shadow_col_1 = tuple(getattr(scene, "genos_shadow_color_1", (0.72, 0.68, 0.75, 1.0)))
        base_shadow_2 = tuple(getattr(scene, "genos_shadow_color_2", (0.42, 0.38, 0.52, 1.0)))
        bounce_tint = tuple(getattr(scene, "genos_cloth_shadow_bounce", (0.70, 0.72, 0.85, 1.0)))
        shadow_col_2 = (
            min(1.0, base_shadow_2[0] * bounce_tint[0] * 1.3),
            min(1.0, base_shadow_2[1] * bounce_tint[1] * 1.3),
            min(1.0, base_shadow_2[2] * bounce_tint[2] * 1.3),
            1.0
        )

    shadow_tint_1 = make_node(nodes, "ShaderNodeMix", "1st Shadow Tint", (loc[0], loc[1] + 150))
    shadow_tint_1.data_type = 'RGBA'
    shadow_tint_1.blend_type = 'MULTIPLY'
    find_socket(shadow_tint_1.inputs, "Factor", "Fac").default_value = 1.0
    link(links, base_color_socket, find_socket(shadow_tint_1.inputs, "A", "Color1"))
    find_socket(shadow_tint_1.inputs, "B", "Color2").default_value = shadow_col_1
    
    shadow_tint_2 = make_node(nodes, "ShaderNodeMix", "2nd Shadow Tint", (loc[0], loc[1] + 50))
    shadow_tint_2.data_type = 'RGBA'
    shadow_tint_2.blend_type = 'MULTIPLY'
    find_socket(shadow_tint_2.inputs, "Factor", "Fac").default_value = 1.0
    link(links, base_color_socket, find_socket(shadow_tint_2.inputs, "A", "Color1"))
    find_socket(shadow_tint_2.inputs, "B", "Color2").default_value = shadow_col_2

    mix_deep_mid = make_node(nodes, "ShaderNodeMix", "Blend Shadow Bands", (loc[0] + 200, loc[1] + 50))
    mix_deep_mid.data_type = 'RGBA'
    mix_deep_mid.blend_type = 'MIX'
    link(links, shadow_step_2.outputs[0], find_socket(mix_deep_mid.inputs, "Factor", "Fac"))
    link(links, find_socket(shadow_tint_2.outputs, "Result", "Color"), find_socket(mix_deep_mid.inputs, "A", "Color1"))
    link(links, find_socket(shadow_tint_1.outputs, "Result", "Color"), find_socket(mix_deep_mid.inputs, "B", "Color2"))

    apply_shadow = make_node(nodes, "ShaderNodeMix", "Apply 3-Band Shading", (loc[0] + 450, loc[1]))
    apply_shadow.data_type = 'RGBA'
    apply_shadow.blend_type = 'MIX'
    link(links, shadow_step_1.outputs[0], find_socket(apply_shadow.inputs, "Factor", "Fac"))
    link(links, find_socket(mix_deep_mid.outputs, "Result", "Color"), find_socket(apply_shadow.inputs, "A", "Color1"))
    link(links, base_color_socket, find_socket(apply_shadow.inputs, "B", "Color2"))
    
    current_shaded_output = find_socket(apply_shadow.outputs, "Result", "Color")

    # SSS Terminator Fringe
    if bool(getattr(scene, "genos_terminator_fringe_enable", True)):
        dist_from_edge = make_node(nodes, "ShaderNodeMath", "Terminator Edge Dist", (loc[0] + 200, loc[1] - 150))
        dist_from_edge.operation = 'SUBTRACT'
        link(links, shadow_step_1.outputs[0], dist_from_edge.inputs[0])
        dist_from_edge.inputs[1].default_value = 0.5
        
        abs_dist = make_node(nodes, "ShaderNodeMath", "Terminator Abs Dist", (loc[0] + 350, loc[1] - 150))
        abs_dist.operation = 'ABSOLUTE'
        link(links, dist_from_edge.outputs[0], abs_dist.inputs[0])
        
        edge_mult = make_node(nodes, "ShaderNodeMath", "Terminator Fringe Falloff", (loc[0] + 500, loc[1] - 150))
        edge_mult.operation = 'MULTIPLY'
        link(links, abs_dist.outputs[0], edge_mult.inputs[0])
        edge_mult.inputs[1].default_value = 2.4
        
        edge_inv = make_node(nodes, "ShaderNodeMath", "Terminator Fringe Mask", (loc[0] + 650, loc[1] - 150))
        edge_inv.operation = 'SUBTRACT'
        edge_inv.inputs[0].default_value = 1.0
        link(links, edge_mult.outputs[0], edge_inv.inputs[1])
        
        edge_clamp = make_node(nodes, "ShaderNodeClamp", "Terminator Fringe Clamp", (loc[0] + 800, loc[1] - 150))
        link(links, edge_inv.outputs[0], edge_clamp.inputs[0])
        try:
            edge_clamp.inputs[1].default_value = 0.0
            edge_clamp.inputs[2].default_value = 1.0
        except Exception:
            pass
            
        fringe_intensity = float(getattr(scene, "genos_terminator_fringe_intensity", 0.40))
        if is_face:
            fringe_intensity *= 0.35
            fringe_color = (1.0, 0.55, 0.45, 1.0) # Gentle peach skin blush
        elif is_hair:
            fringe_intensity = 0.0 # Suppressed on hair to prevent burning neon ahoge seam
            fringe_color = (0.0, 0.0, 0.0, 1.0)
        else:
            fringe_color = tuple(getattr(scene, "genos_terminator_fringe_color", (1.0, 0.35, 0.25, 1.0)))

        edge_scale = make_node(nodes, "ShaderNodeMath", "Scale Terminator Fringe", (loc[0] + 950, loc[1] - 150))
        edge_scale.operation = 'MULTIPLY'
        link(links, edge_clamp.outputs[0], edge_scale.inputs[0])
        edge_scale.inputs[1].default_value = fringe_intensity
        
        add_fringe = make_node(nodes, "ShaderNodeMix", "Add SSS Fringe Glow", (loc[0] + 700, loc[1]))
        add_fringe.data_type = 'RGBA'
        add_fringe.blend_type = 'ADD'
        link(links, edge_scale.outputs[0], find_socket(add_fringe.inputs, "Factor", "Fac"))
        link(links, current_shaded_output, find_socket(add_fringe.inputs, "A", "Color1"))
        find_socket(add_fringe.inputs, "B", "Color2").default_value = fringe_color
        current_shaded_output = find_socket(add_fringe.outputs, "Result", "Color")

    return current_shaded_output, shadow_step_1.outputs[0], shadow_step_2.outputs[0]

def _hair_band_edges(scene):
    low, high = sorted((scene.genos_hair_band2_thresh, scene.genos_hair_band1_thresh))
    high = max(high, low + 0.04)
    feather = min(max(0.001, scene.genos_hair_feather), (high - low) * 0.24)
    return low, high, feather


_GENOS_HAIR_BAND_BATCH_UPDATE = False
_GENOS_HAIR_TOON_BATCH_UPDATE = False


def _hair_band_specs(scene):
    """Return highlight-band settings as plain dictionaries.

    Every dynamic highlight band owns all of its styling controls. Legacy global
    halo controls are only used to initialize old .blend files once.
    """
    collection = getattr(scene, 'genos_hair_bands', None)
    if collection is not None and len(collection) > 0:
        specs = []
        for index, band in enumerate(collection):
            specs.append({
                'index': index,
                'enabled': bool(getattr(band, 'enabled', True)),
                'label': getattr(band, 'label', '') or ('Band %d' % (index + 1)),
                'coord_mode': getattr(band, 'coord_mode', 'GENERATED_Z'),
                'position': float(getattr(band, 'position', 0.5)),
                'width': max(0.001, float(getattr(band, 'width', 0.05))),
                'strength': max(0.0, float(getattr(band, 'strength', 1.0))),
                'opacity': max(0.0, min(1.0, float(getattr(band, 'opacity', 1.0)))),
                'emission_enabled': bool(getattr(band, 'emission_enabled', True)),
                'emission_strength': max(0.0, float(getattr(band, 'emission_strength', 0.60))),
                'color': tuple(getattr(band, 'color', (1.0, 0.95, 0.98, 1.0))),
                'softness': max(0.0, min(1.0, float(getattr(band, 'softness', 0.22)))),
                'blur': max(0.0, min(1.0, float(getattr(band, 'blur', 0.18)))),
                'curve_center': float(getattr(band, 'curve_center', 0.50)),
                'curvature': float(getattr(band, 'curvature', 0.18)),
                'spot_density': max(0.25, float(getattr(band, 'spot_density', 9.0))),
                'spot_gap': max(0.0, min(0.48, float(getattr(band, 'spot_gap', 0.22)))),
                'spot_shape': getattr(band, 'spot_shape', 'ROUND'),
                'spot_aspect': max(0.20, min(4.0, float(getattr(band, 'spot_aspect', 1.0)))),
                'strand_detail': max(0.0, min(1.0, float(getattr(band, 'strand_detail', 0.35)))),
            })
        return specs

    # Legacy fallback. Every migrated band receives its own copy of the former globals.
    count = max(1, int(getattr(scene, 'genos_hair_halo_count', 2)))
    legacy = []
    for i in range(1, count + 1):
        if i == 1:
            position = float(getattr(scene, 'genos_hair_halo_pos', 0.69))
            width = float(getattr(scene, 'genos_hair_halo_width', 0.05))
            strength = 1.0
        else:
            position = float(getattr(scene, 'genos_hair_halo%d_pos' % i, 0.60 - 0.10 * (i - 2)))
            width = float(getattr(scene, 'genos_hair_halo%d_width' % i, 0.03))
            strength = float(getattr(scene, 'genos_hair_halo%d_strength' % i, 0.55))
        color = tuple(getattr(scene, 'genos_hair_halo%d_color' % i,
                              (1.0, 0.95 - 0.12 * min(i - 1, 3), 0.98 - 0.10 * min(i - 1, 3), 1.0)))
        legacy.append({
            'index': i - 1, 'enabled': True, 'label': 'Band %d' % i,
            'coord_mode': getattr(scene, 'genos_hair_halo_coord_mode', 'GENERATED_Z'),
            'position': position, 'width': max(0.001, width), 'strength': max(0.0, strength),
            'opacity': 1.0, 'emission_enabled': True, 'emission_strength': 0.60,
            'color': color,
            'softness': float(getattr(scene, 'genos_hair_halo_softness', 0.22)),
            'blur': float(getattr(scene, 'genos_hair_halo_blur', 0.18)),
            'curve_center': float(getattr(scene, 'genos_hair_halo_curve_center', 0.50)),
            'curvature': float(getattr(scene, 'genos_hair_halo_curvature', 0.18)),
            'spot_density': float(getattr(scene, 'genos_hair_halo_spot_density', 9.0)),
            'spot_gap': float(getattr(scene, 'genos_hair_halo_spot_gap', 0.22)),
            'spot_shape': getattr(scene, 'genos_hair_halo_spot_shape', 'ROUND'),
            'spot_aspect': float(getattr(scene, 'genos_hair_halo_spot_aspect', 1.0)),
            'strand_detail': 0.35,
        })
    return legacy


def _assign_hair_band_from_spec(band, spec, index=0):
    band.label = str(spec.get('label', 'Band %d' % (index + 1)))
    band.enabled = bool(spec.get('enabled', True))
    band.coord_mode = spec.get('coord_mode', 'GENERATED_Z')
    band.position = float(spec.get('position', 0.5 - index * 0.08))
    band.width = max(0.001, float(spec.get('width', 0.05)))
    band.strength = max(0.0, float(spec.get('strength', 1.0 if index == 0 else 0.55)))
    band.opacity = max(0.0, min(1.0, float(spec.get('opacity', 1.0))))
    band.emission_enabled = bool(spec.get('emission_enabled', True))
    band.emission_strength = max(0.0, float(spec.get('emission_strength', 0.60)))
    band.color = tuple(spec.get('color', (1.0, 0.95, 0.98, 1.0)))
    band.softness = max(0.0, min(1.0, float(spec.get('softness', 0.22))))
    band.blur = max(0.0, min(1.0, float(spec.get('blur', 0.18))))
    band.curve_center = float(spec.get('curve_center', 0.50))
    band.curvature = float(spec.get('curvature', 0.18))
    band.spot_density = max(0.25, float(spec.get('spot_density', 9.0)))
    band.spot_gap = max(0.0, min(0.48, float(spec.get('spot_gap', 0.22))))
    band.spot_shape = spec.get('spot_shape', 'ROUND')
    band.spot_aspect = max(0.20, min(4.0, float(spec.get('spot_aspect', 1.0))))
    band.strand_detail = max(0.0, min(1.0, float(spec.get('strand_detail', 0.35))))


def _set_dynamic_hair_bands(scene, specs):
    """Replace highlight-band collection without firing callbacks for each field."""
    global _GENOS_HAIR_BAND_BATCH_UPDATE
    collection = getattr(scene, 'genos_hair_bands', None)
    if collection is None:
        return
    _GENOS_HAIR_BAND_BATCH_UPDATE = True
    try:
        collection.clear()
        for i, spec in enumerate(specs):
            _assign_hair_band_from_spec(collection.add(), spec, i)
        if hasattr(scene, 'genos_hair_band_index'):
            scene.genos_hair_band_index = max(0, min(len(collection) - 1, int(getattr(scene, 'genos_hair_band_index', 0))))
        if hasattr(scene, 'genos_hair_halo_count'):
            scene.genos_hair_halo_count = max(1, min(128, len(collection)))
    finally:
        _GENOS_HAIR_BAND_BATCH_UPDATE = False


def _ensure_dynamic_hair_bands(scene):
    collection = getattr(scene, 'genos_hair_bands', None)
    if collection is None or len(collection) > 0:
        return
    _set_dynamic_hair_bands(scene, _hair_band_specs(scene))


def _hair_toon_specs(scene):
    """Return ordered toon shadow bands. Order is shallow/lit-side -> deepest."""
    collection = getattr(scene, 'genos_hair_toon_bands', None)
    if collection is not None and len(collection) > 0:
        out = []
        for i, band in enumerate(collection):
            out.append({
                'index': i,
                'label': getattr(band, 'label', '') or ('Toon Band %d' % (i + 1)),
                'threshold': max(0.0, min(1.0, float(getattr(band, 'threshold', 0.5)))),
                'feather': max(0.0001, min(0.5, float(getattr(band, 'feather', 0.02)))),
                'strength': max(0.0, min(1.0, float(getattr(band, 'strength', 1.0)))),
                'color': tuple(getattr(band, 'color', (0.75, 0.55, 0.62, 1.0))),
            })
        return out
    return [
        {'index': 0, 'label': 'Mid Shadow', 'threshold': float(getattr(scene, 'genos_hair_band1_thresh', 0.47)),
         'feather': float(getattr(scene, 'genos_hair_feather', 0.02)), 'strength': 1.0,
         'color': tuple(getattr(scene, 'genos_hair_shadow_color_1', (0.76, 0.50, 0.58, 1.0)))},
        {'index': 1, 'label': 'Deep Shadow', 'threshold': float(getattr(scene, 'genos_hair_band2_thresh', 0.20)),
         'feather': float(getattr(scene, 'genos_hair_feather', 0.02)), 'strength': 1.0,
         'color': tuple(getattr(scene, 'genos_hair_shadow_color_2', (0.42, 0.20, 0.30, 1.0)))},
    ]


def _set_dynamic_hair_toon_bands(scene, specs):
    global _GENOS_HAIR_TOON_BATCH_UPDATE
    collection = getattr(scene, 'genos_hair_toon_bands', None)
    if collection is None:
        return
    _GENOS_HAIR_TOON_BATCH_UPDATE = True
    try:
        collection.clear()
        for i, spec in enumerate(specs):
            band = collection.add()
            band.label = str(spec.get('label', 'Toon Band %d' % (i + 1)))
            band.threshold = max(0.0, min(1.0, float(spec.get('threshold', max(0.05, 0.5 - i * 0.18)))))
            band.feather = max(0.0001, min(0.5, float(spec.get('feather', 0.02))))
            band.strength = max(0.0, min(1.0, float(spec.get('strength', 1.0))))
            band.color = tuple(spec.get('color', (0.75, 0.55, 0.62, 1.0)))
        if hasattr(scene, 'genos_hair_toon_band_index'):
            scene.genos_hair_toon_band_index = max(0, min(len(collection) - 1, int(getattr(scene, 'genos_hair_toon_band_index', 0))))
    finally:
        _GENOS_HAIR_TOON_BATCH_UPDATE = False


def _ensure_dynamic_hair_toon_bands(scene):
    collection = getattr(scene, 'genos_hair_toon_bands', None)
    if collection is None or len(collection) > 0:
        return
    _set_dynamic_hair_toon_bands(scene, _hair_toon_specs(scene))


def _build_dynamic_hair_toon_shading(nodes, links, scene, light_socket, base_color_socket, loc=(-300, 0)):
    """Build arbitrary hair cel-shadow bands.

    Collection order is shallow -> deep. Each band defines the threshold where its
    shadow region begins, its own edge feather, tint and tint strength.
    """
    specs = _hair_toon_specs(scene)
    if not specs:
        one = make_node(nodes, 'ShaderNodeValue', 'Hair Toon Lit Mask', (loc[0], loc[1] - 800))
        one.outputs[0].default_value = 1.0
        return base_color_socket, one.outputs[0], one.outputs[0]

    tint_outputs = []
    step_outputs = []
    for i, spec in enumerate(specs, 1):
        y = loc[1] - 780 - (i - 1) * 150
        step = make_node(nodes, 'ShaderNodeMapRange', 'Hair Toon %d Step' % i, (loc[0] - 240, y))
        configure_smooth_step(step, spec['threshold'] - spec['feather'], spec['threshold'] + spec['feather'])
        link(links, light_socket, step.inputs[0])
        step_outputs.append(step.outputs[0])

        tint = make_node(nodes, 'ShaderNodeMix', 'Hair Toon %d Tint' % i, (loc[0], y + 30))
        tint.data_type = 'RGBA'; tint.blend_type = 'MULTIPLY'
        find_socket(tint.inputs, 'Factor', 'Fac').default_value = spec['strength']
        link(links, base_color_socket, find_socket(tint.inputs, 'A', 'Color1'))
        find_socket(tint.inputs, 'B', 'Color2').default_value = spec['color']
        tint_outputs.append(find_socket(tint.outputs, 'Result', 'Color'))

    # Start at the deepest color. Each deeper threshold transitions upward into the
    # previous shallower tint. The first threshold finally transitions into lit base.
    current = tint_outputs[-1]
    for idx in range(len(specs) - 1, 0, -1):
        mix = make_node(nodes, 'ShaderNodeMix', 'Hair Toon Transition %d' % (idx + 1),
                        (loc[0] + 240 + (len(specs) - idx) * 170, loc[1] - 100 - idx * 60))
        mix.data_type = 'RGBA'; mix.blend_type = 'MIX'
        link(links, step_outputs[idx], find_socket(mix.inputs, 'Factor', 'Fac'))
        link(links, current, find_socket(mix.inputs, 'A', 'Color1'))
        link(links, tint_outputs[idx - 1], find_socket(mix.inputs, 'B', 'Color2'))
        current = find_socket(mix.outputs, 'Result', 'Color')

    lit_mix = make_node(nodes, 'ShaderNodeMix', 'Apply Dynamic Hair Toon Shading', (loc[0] + 620, loc[1]))
    lit_mix.data_type = 'RGBA'; lit_mix.blend_type = 'MIX'
    link(links, step_outputs[0], find_socket(lit_mix.inputs, 'Factor', 'Fac'))
    link(links, current, find_socket(lit_mix.inputs, 'A', 'Color1'))
    link(links, base_color_socket, find_socket(lit_mix.inputs, 'B', 'Color2'))
    shaded = find_socket(lit_mix.outputs, 'Result', 'Color')

    first = step_outputs[0]
    second = step_outputs[1] if len(step_outputs) > 1 else first
    return shaded, first, second


def _update_hair_toon_live(scene, context=None, only_mat=None):
    if globals().get('_GENOS_HAIR_TOON_BATCH_UPDATE', False):
        return
    specs = _hair_toon_specs(scene)
    if only_mat is not None:
        materials = [only_mat]
    else:
        materials = {slot.material for obj in scene.objects for slot in obj.material_slots
                     if slot.material and slot.material.use_nodes}
    for mat in materials:
        if not mat or not getattr(mat, 'use_nodes', False):
            continue
        nodes = mat.node_tree.nodes
        if nodes.get('Apply Dynamic Hair Toon Shading') is None:
            continue
        for i, spec in enumerate(specs, 1):
            step = nodes.get('Hair Toon %d Step' % i)
            if step is not None:
                configure_smooth_step(step, spec['threshold'] - spec['feather'], spec['threshold'] + spec['feather'])
            tint = nodes.get('Hair Toon %d Tint' % i)
            if tint is not None:
                find_socket(tint.inputs, 'Factor', 'Fac').default_value = spec['strength']
                find_socket(tint.inputs, 'B', 'Color2').default_value = spec['color']
        try: mat.node_tree.update_tag()
        except Exception: pass


def _rebuild_dynamic_hair_materials(context):
    """Rebuild realtime HAIR graphs after structural highlight/toon band changes."""
    materials = []
    seen = set()
    obj = getattr(context, 'active_object', None) if context else None
    if obj and getattr(obj, 'type', None) == 'MESH':
        for slot in obj.material_slots:
            mat = slot.material
            if mat and mat.name not in seen and (mat.get('genos_shader_type') == 'HAIR' or 'hair' in mat.name.lower()):
                materials.append(mat); seen.add(mat.name)
    if not materials:
        try:
            source = bpy.data.materials
        except Exception:
            source = []
        for mat in source:
            if mat and mat.name not in seen and getattr(mat, 'use_nodes', False):
                if mat.get('genos_shader_type') == 'HAIR' or 'hair' in mat.name.lower() or mat.node_tree.nodes.get('Hair Facing Weight'):
                    materials.append(mat); seen.add(mat.name)

    count = 0
    for mat in materials:
        if 'is_anime_toon_baked' in mat:
            continue
        try:
            if adapt_material_to_new_scheme(mat):
                count += 1
        except Exception as exc:
            print('[Anime Studio] Dynamic hair graph rebuild failed for %s: %s' % (mat.name, exc))
    return count


def _build_clean_halos(nodes, links, scene, elevation, strands, facing):
    """Build unlimited independent hair highlight / angel-ring bands efficiently.

    v18.10 optimization:
    - TexCoord, Geometry, coordinate splitting and front-facing falloff are shared by all bands.
    - Circular distance uses a single VectorMath LENGTH instead of the old square/add/sqrt chain.
    - Enabled + opacity + visible strength are folded into one live gain node.
    - Emission reuses the already-gated visible mask instead of rebuilding opacity/facing chains.

    Every band still keeps independent position, width, color, curve, softness, blur,
    spot shape/density/gap/aspect, strand detail, opacity and emission controls.
    """
    specs = _hair_band_specs(scene)
    combined = None
    color_output = None
    emission_mask_output = None
    emission_color_output = None

    # Shared coordinates: build once, then every band can switch coordinate mode live.
    tex = make_node(nodes, 'ShaderNodeTexCoord', 'Hair Bands Shared Coordinates', (-1700, -1680))
    geo = make_node(nodes, 'ShaderNodeNewGeometry', 'Hair Bands Shared Geometry', (-1700, -1840))
    gen_sep = make_node(nodes, 'ShaderNodeSeparateXYZ', 'Hair Bands Generated XYZ', (-1510, -1680))
    uv_sep = make_node(nodes, 'ShaderNodeSeparateXYZ', 'Hair Bands UV XYZ', (-1510, -1770))
    obj_sep = make_node(nodes, 'ShaderNodeSeparateXYZ', 'Hair Bands Object XYZ', (-1510, -1860))
    norm_sep = make_node(nodes, 'ShaderNodeSeparateXYZ', 'Hair Bands Normal XYZ', (-1510, -1950))
    link(links, tex.outputs['Generated'], gen_sep.inputs[0])
    link(links, tex.outputs['UV'], uv_sep.inputs[0])
    link(links, tex.outputs['Object'], obj_sep.inputs[0])
    link(links, geo.outputs['Normal'], norm_sep.inputs[0])

    # Identical front-facing falloff is shared by every band.
    front = make_node(nodes, 'ShaderNodeMapRange', 'Hair Bands Shared Front Facing', (-1320, -2040))
    configure_smooth_step(front, 0.30, 0.95, 1.0, 0.0)
    link(links, facing, front.inputs[0])
    front_socket = front.outputs[0]
    axis_x = gen_sep.outputs['X']

    def elevation_for(mode):
        if mode == 'UV_V':
            return uv_sep.outputs['Y']
        if mode == 'NORMAL_Z':
            return norm_sep.outputs['Z']
        if mode == 'OBJECT_Z':
            return obj_sep.outputs['Z']
        return gen_sep.outputs['Z']

    for zero_index, spec in enumerate(specs):
        i = zero_index + 1
        prefix = 'Clean Halo %d ' % i
        y = -1700 - zero_index * 650
        enabled = bool(spec['enabled'])
        width = max(0.001, spec['width'])
        softness = spec['softness']
        blur = spec['blur']
        density = spec['spot_density']
        gap = spec['spot_gap']
        shape = spec['spot_shape']
        aspect = 1.0 if shape == 'ROUND' else spec['spot_aspect']
        elev = elevation_for(spec['coord_mode'])

        centered = make_node(nodes, 'ShaderNodeMath', prefix + 'Curve Center', (-1260, y - 40))
        centered.operation = 'SUBTRACT'
        link(links, axis_x, centered.inputs[0])
        centered.inputs[1].default_value = spec['curve_center']

        square = make_node(nodes, 'ShaderNodeMath', prefix + 'Curve Parabola', (-1080, y - 40))
        square.operation = 'MULTIPLY'
        link(links, centered.outputs[0], square.inputs[0])
        link(links, centered.outputs[0], square.inputs[1])

        # MULTIPLY_ADD = parabola * curvature + selected elevation.
        curved = make_node(nodes, 'ShaderNodeMath', prefix + 'Curved Elevation', (-900, y - 40))
        curved.operation = 'MULTIPLY_ADD'
        link(links, square.outputs[0], curved.inputs[0])
        curved.inputs[1].default_value = spec['curvature'] * 4.0
        link(links, elev, curved.inputs[2])

        offset = make_node(nodes, 'ShaderNodeMath', prefix + 'Position', (-720, y - 40))
        offset.operation = 'SUBTRACT'
        link(links, curved.outputs[0], offset.inputs[0])
        offset.inputs[1].default_value = spec['position']

        distance = make_node(nodes, 'ShaderNodeMath', prefix + 'Distance', (-540, y - 40))
        distance.operation = 'ABSOLUTE'
        link(links, offset.outputs[0], distance.inputs[0])

        edge = make_node(nodes, 'ShaderNodeMapRange', prefix + 'Edge', (-360, y - 40))
        inner = max(0.0, width * (1.0 - softness - blur * 0.85))
        outer = width * (1.0 + blur * 1.35)
        configure_smooth_step(edge, inner, outer, 1.0, 0.0)
        link(links, distance.outputs[0], edge.inputs[0])

        # Shared generated-X axis, independent frequency per band.
        freq = make_node(nodes, 'ShaderNodeMath', prefix + 'Spot Density', (-1080, y - 250))
        freq.operation = 'MULTIPLY'
        link(links, axis_x, freq.inputs[0])
        freq.inputs[1].default_value = density
        frac = make_node(nodes, 'ShaderNodeMath', prefix + 'Spot Fraction', (-900, y - 250))
        frac.operation = 'FRACT'; link(links, freq.outputs[0], frac.inputs[0])
        cent = make_node(nodes, 'ShaderNodeMath', prefix + 'Spot Center', (-720, y - 250))
        cent.operation = 'SUBTRACT'; link(links, frac.outputs[0], cent.inputs[0]); cent.inputs[1].default_value = 0.5
        xdist = make_node(nodes, 'ShaderNodeMath', prefix + 'Spot X Distance', (-540, y - 250))
        xdist.operation = 'ABSOLUTE'; link(links, cent.outputs[0], xdist.inputs[0])

        half_width = max(0.02, 0.50 - gap * 0.48)

        # Dashed ribbon path. Kept alongside the round path so Spot Shape remains realtime.
        ribbon = make_node(nodes, 'ShaderNodeMapRange', prefix + 'Ribbon Cell', (-180, y - 180))
        ribbon_feather = 0.015 + blur * 0.18
        configure_smooth_step(ribbon, max(0.0, half_width - ribbon_feather), half_width + ribbon_feather,
                              1.0, min(0.55, blur * 0.35))
        link(links, xdist.outputs[0], ribbon.inputs[0])
        ribbon_detail = make_node(nodes, 'ShaderNodeMath', prefix + 'Ribbon Spots', (0, y - 120))
        ribbon_detail.operation = 'MULTIPLY'
        link(links, edge.outputs[0], ribbon_detail.inputs[0])
        link(links, ribbon.outputs[0], ribbon_detail.inputs[1])

        # Circular / oval path: two divides + CombineXYZ + LENGTH replaces x²+y²+sqrt.
        x_radius = max(0.035, half_width * aspect)
        x_norm = make_node(nodes, 'ShaderNodeMath', prefix + 'Circle X Radius', (-360, y - 330))
        x_norm.operation = 'DIVIDE'; link(links, xdist.outputs[0], x_norm.inputs[0]); x_norm.inputs[1].default_value = x_radius
        y_norm = make_node(nodes, 'ShaderNodeMath', prefix + 'Circle Y Radius', (-360, y - 430))
        y_norm.operation = 'DIVIDE'; link(links, distance.outputs[0], y_norm.inputs[0]); y_norm.inputs[1].default_value = width
        radial_vec = make_node(nodes, 'ShaderNodeCombineXYZ', prefix + 'Circle Vector', (-160, y - 380))
        link(links, x_norm.outputs[0], radial_vec.inputs['X']); link(links, y_norm.outputs[0], radial_vec.inputs['Y'])
        radial = make_node(nodes, 'ShaderNodeVectorMath', prefix + 'Circle Radius', (40, y - 380))
        radial.operation = 'LENGTH'; link(links, radial_vec.outputs[0], radial.inputs[0])
        circle = make_node(nodes, 'ShaderNodeMapRange', prefix + 'Circle Edge', (240, y - 380))
        circle_feather = 0.025 + softness * 0.20 + blur * 0.40
        configure_smooth_step(circle, max(0.0, 1.0 - circle_feather), 1.0 + circle_feather, 1.0, 0.0)
        link(links, radial.outputs['Value'], circle.inputs[0])

        shape_mix = make_node(nodes, 'ShaderNodeMix', prefix + 'Spot Shape', (240, y - 160))
        shape_mix.data_type = 'FLOAT'
        find_socket(shape_mix.inputs, 'Factor').default_value = 0.0 if shape == 'RIBBON' else 1.0
        link(links, ribbon_detail.outputs[0], find_socket(shape_mix.inputs, 'A'))
        link(links, circle.outputs[0], find_socket(shape_mix.inputs, 'B'))

        # Independent strand breakup, using the single shared strand texture from the hair shader.
        strand_mix = make_node(nodes, 'ShaderNodeMix', prefix + 'Strand Detail', (440, y - 230))
        strand_mix.data_type = 'FLOAT'
        find_socket(strand_mix.inputs, 'Factor').default_value = spec['strand_detail'] * (1.0 - blur * 0.85)
        find_socket(strand_mix.inputs, 'A').default_value = 1.0
        link(links, strands, find_socket(strand_mix.inputs, 'B'))
        detailed = make_node(nodes, 'ShaderNodeMath', prefix + 'Detailed Mask', (620, y - 160))
        detailed.operation = 'MULTIPLY'
        link(links, find_socket(shape_mix.outputs, 'Result'), detailed.inputs[0])
        link(links, find_socket(strand_mix.outputs, 'Result'), detailed.inputs[1])

        # One effective visible gain replaces Enabled -> Opacity -> Strength chains.
        visible_gain = make_node(nodes, 'ShaderNodeMath', prefix + 'Visible Gain', (800, y - 160))
        visible_gain.operation = 'MULTIPLY'
        link(links, detailed.outputs[0], visible_gain.inputs[0])
        visible_gain.inputs[1].default_value = (spec['strength'] * spec['opacity']) if enabled else 0.0

        gated = make_node(nodes, 'ShaderNodeMath', prefix + 'Facing Gate', (980, y - 160))
        gated.operation = 'MULTIPLY'
        link(links, visible_gain.outputs[0], gated.inputs[0]); link(links, front_socket, gated.inputs[1])

        if combined is None:
            combined = gated.outputs[0]
        else:
            union = make_node(nodes, 'ShaderNodeMath', prefix + 'Union', (1160, y - 160))
            union.operation = 'MAXIMUM'
            link(links, combined, union.inputs[0]); link(links, gated.outputs[0], union.inputs[1])
            combined = union.outputs[0]

        col = make_node(nodes, 'ShaderNodeRGB', prefix + 'Color', (800, y + 10))
        col.outputs[0].default_value = spec['color']
        if color_output is None:
            color_output = col.outputs[0]
        else:
            color_mix = make_node(nodes, 'ShaderNodeMix', prefix + 'Color Mix', (1160, y + 10))
            color_mix.data_type = 'RGBA'; color_mix.blend_type = 'MIX'
            link(links, gated.outputs[0], find_socket(color_mix.inputs, 'Factor', 'Fac'))
            link(links, color_output, find_socket(color_mix.inputs, 'A', 'Color1'))
            link(links, col.outputs[0], find_socket(color_mix.inputs, 'B', 'Color2'))
            color_output = find_socket(color_mix.outputs, 'Result', 'Color')

        # Emission reuses the visible mask, so opacity/strength/facing are evaluated once.
        emit_enabled = make_node(nodes, 'ShaderNodeMath', prefix + 'Emission Enabled', (1160, y - 360))
        emit_enabled.operation = 'MULTIPLY'
        link(links, gated.outputs[0], emit_enabled.inputs[0])
        emit_enabled.inputs[1].default_value = 1.0 if (enabled and spec['emission_enabled']) else 0.0

        emit_gain = make_node(nodes, 'ShaderNodeMath', prefix + 'Emission Strength', (1340, y - 360))
        emit_gain.operation = 'MULTIPLY'
        link(links, emit_enabled.outputs[0], emit_gain.inputs[0])
        emit_gain.inputs[1].default_value = spec['emission_strength'] if (enabled and spec['emission_enabled']) else 0.0

        emit_col = make_node(nodes, 'ShaderNodeMix', prefix + 'Emission Color', (1520, y - 330))
        emit_col.data_type = 'RGBA'; emit_col.blend_type = 'MULTIPLY'
        find_socket(emit_col.inputs, 'Factor', 'Fac').default_value = 1.0
        link(links, col.outputs[0], find_socket(emit_col.inputs, 'A', 'Color1'))
        link(links, emit_gain.outputs[0], find_socket(emit_col.inputs, 'B', 'Color2'))

        if emission_mask_output is None:
            emission_mask_output = emit_enabled.outputs[0]
        else:
            emit_union = make_node(nodes, 'ShaderNodeMath', prefix + 'Emission Union', (1520, y - 470))
            emit_union.operation = 'MAXIMUM'
            link(links, emission_mask_output, emit_union.inputs[0]); link(links, emit_enabled.outputs[0], emit_union.inputs[1])
            emission_mask_output = emit_union.outputs[0]

        if emission_color_output is None:
            emission_color_output = find_socket(emit_col.outputs, 'Result', 'Color')
        else:
            emit_add = make_node(nodes, 'ShaderNodeMix', prefix + 'Emission Add', (1700, y - 330))
            emit_add.data_type = 'RGBA'; emit_add.blend_type = 'ADD'
            find_socket(emit_add.inputs, 'Factor', 'Fac').default_value = 1.0
            link(links, emission_color_output, find_socket(emit_add.inputs, 'A', 'Color1'))
            link(links, find_socket(emit_col.outputs, 'Result', 'Color'), find_socket(emit_add.inputs, 'B', 'Color2'))
            emission_color_output = find_socket(emit_add.outputs, 'Result', 'Color')

    if combined is None:
        zero = make_node(nodes, 'ShaderNodeValue', 'Clean Halo Empty Mask', (1050, -1500)); zero.outputs[0].default_value = 0.0
        combined = zero.outputs[0]
    if color_output is None:
        fallback = make_node(nodes, 'ShaderNodeRGB', 'Clean Halo Empty Color', (1050, -1370)); fallback.outputs[0].default_value = (1.0, 0.95, 0.98, 1.0)
        color_output = fallback.outputs[0]
    if emission_mask_output is None:
        zero_emit = make_node(nodes, 'ShaderNodeValue', 'Clean Halo Empty Emission Mask', (1050, -1240)); zero_emit.outputs[0].default_value = 0.0
        emission_mask_output = zero_emit.outputs[0]
    if emission_color_output is None:
        zero_emit_color = make_node(nodes, 'ShaderNodeRGB', 'Clean Halo Empty Emission Color', (1050, -1110)); zero_emit_color.outputs[0].default_value = (0.0, 0.0, 0.0, 1.0)
        emission_color_output = zero_emit_color.outputs[0]

    # Reroutes are compile-time pass-throughs: stable names for the common emission system
    # without adding two extra math/mix shader operations.
    ilm_mask = make_node(nodes, 'NodeReroute', 'Hair Bands ILM Emission Mask', (1880, -2050))
    link(links, emission_mask_output, ilm_mask.inputs[0])
    ilm_color = make_node(nodes, 'NodeReroute', 'Hair Bands ILM Emission Color', (1880, -1920))
    link(links, emission_color_output, ilm_color.inputs[0])

    return combined, color_output

def _build_anime_specular_system(nodes, links, scene, shader_type, gbw_light_socket, ilm_spec_bw_socket, light_step_socket, base_color_socket, roughness_socket=None, metallic_socket=None, loc=(-500, -700)):
    """
    Dual-Tier Anime Specularity:
    - FACE: Zero specular (Anime skin is velvety matte cel diffuse)
    - HAIR: 3D Anisotropic Angel Ring (Halo across head crown) + discrete cel glints
    - DEFAULT/METALLIC: Dual-tier crisp core glint + soft sheen halo with light gating
    """
    if shader_type == 'FACE':
        # Anime face skin NEVER has glossy specular highlights (prevents plastic mannequin look)
        zero_val = make_node(nodes, "ShaderNodeValue", "Zero Face Specular", (loc[0] + 200, loc[1]))
        zero_val.outputs[0].default_value = 0.0
        zero_col = make_node(nodes, "ShaderNodeRGB", "Zero Face Spec Color", (loc[0] + 400, loc[1]))
        zero_col.outputs[0].default_value = (0.0, 0.0, 0.0, 1.0)
        return zero_col.outputs[0], zero_val.outputs[0]

    core_strength = float(getattr(scene, "genos_spec_core_strength", 0.75))
    halo_strength = float(getattr(scene, "genos_spec_halo_strength", 0.40))
    is_metallic = bool(getattr(scene, "genos_spec_metallic", False)) or (shader_type == 'METALLIC')
    cloth_mode = getattr(scene, "genos_cloth_spec_mode", "HYBRID_AUTO")
    
    if shader_type == 'HAIR':
        # Optimized v18.10 hair highlight engine.
        # The obsolete pre-dynamic halo chain was removed; only the strand source,
        # dynamic bands and optional core glint remain in the compiled shader.
        strands_strength = max(0.0, float(getattr(scene, "genos_hair_strands_strength", 0.65)))
        hair_highlight_str = max(0.0, float(getattr(scene, "genos_hair_highlight_strength", 0.85)))
        strands_scale = float(getattr(scene, "genos_hair_strands_scale", 24.0))

        tex_coord = make_node(nodes, "ShaderNodeTexCoord", "Hair UV Coord", (loc[0], loc[1] - 520))
        wave_strands = make_node(nodes, "ShaderNodeTexWave", "Hair Strand Cuts", (loc[0] + 180, loc[1] - 520))
        wave_strands.wave_type = 'BANDS'; wave_strands.bands_direction = 'X'
        wave_strands.inputs["Scale"].default_value = strands_scale
        wave_strands.inputs["Distortion"].default_value = 1.2
        link(links, tex_coord.outputs["UV"], wave_strands.inputs["Vector"])

        strand_step = make_node(nodes, "ShaderNodeMapRange", "Strand Glint Steps", (loc[0] + 360, loc[1] - 520))
        configure_smooth_step(strand_step, 0.25, 0.65)
        link(links, wave_strands.outputs["Color"], strand_step.inputs[0])

        mod_strands = make_node(nodes, "ShaderNodeMath", "Modulate Strands Scale", (loc[0] + 540, loc[1] - 520))
        mod_strands.operation = 'MULTIPLY'; mod_strands.inputs[1].default_value = strands_strength
        link(links, strand_step.outputs[0], mod_strands.inputs[0])

        bias_strands = make_node(nodes, "ShaderNodeMath", "Strands Bias", (loc[0] + 700, loc[1] - 520))
        bias_strands.operation = 'ADD'; bias_strands.inputs[1].default_value = max(0.2, 1.0 - strands_strength * 0.7)
        link(links, mod_strands.outputs[0], bias_strands.inputs[0])

        facing = make_node(nodes, "ShaderNodeLayerWeight", "Hair Facing Weight", (loc[0] + 700, loc[1] - 180))
        facing.inputs["Blend"].default_value = 0.35

        pbr_spec_str = max(0.0, float(getattr(scene, "genos_hair_pbr_spec_str", 0.0)))
        core_glint = make_node(nodes, "ShaderNodeMapRange", "Peak Light Glint", (loc[0] + 400, loc[1]))
        configure_smooth_step(core_glint, 0.72, 0.78)
        link(links, gbw_light_socket, core_glint.inputs[0])
        core_scaled = make_node(nodes, "ShaderNodeMath", "Scale Peak Glint", (loc[0] + 600, loc[1]))
        core_scaled.operation = 'MULTIPLY'; core_scaled.inputs[1].default_value = core_strength * 0.4 * pbr_spec_str
        link(links, core_glint.outputs[0], core_scaled.inputs[0])

        clean_halos, clean_halo_color = _build_clean_halos(
            nodes, links, scene, None, bias_strands.outputs[0], facing.outputs['Facing']
        )
        halo_scaled = make_node(nodes, "ShaderNodeMath", "Scale Angel Ring", (loc[0] + 1160, loc[1] - 320))
        halo_scaled.operation = 'MULTIPLY'; halo_scaled.inputs[1].default_value = hair_highlight_str
        link(links, clean_halos, halo_scaled.inputs[0])

        comb_hair = make_node(nodes, "ShaderNodeMath", "Combine Hair Highlights", (loc[0] + 1340, loc[1] - 220))
        comb_hair.operation = 'MAXIMUM'
        link(links, halo_scaled.outputs[0], comb_hair.inputs[0]); link(links, core_scaled.outputs[0], comb_hair.inputs[1])
        spec_with_features = comb_hair.outputs[0]
    else:
        # Authentic 2D Anime Clothing & Armor Specular Engine
        cloth_mode = getattr(scene, "genos_cloth_spec_mode", "HYBRID_AUTO")
        velvet_sheen_str = float(getattr(scene, "genos_cloth_velvet_sheen", 0.40))
        velvet_power = float(getattr(scene, "genos_cloth_velvet_power", 2.8))
        cloth_core_str = float(getattr(scene, "genos_cloth_spec_str", 0.45))

        # 1. 2D Matte Fabric Velvet Sheen (Soft microfiber grazing falloff)
        velvet_facing = make_node(nodes, "ShaderNodeLayerWeight", "Cloth Velvet Weight", (loc[0] + 100, loc[1] - 300))
        velvet_facing.inputs["Blend"].default_value = 0.20
        
        inv_velvet = make_node(nodes, "ShaderNodeMath", "Invert Velvet Facing", (loc[0] + 260, loc[1] - 300))
        inv_velvet.operation = 'SUBTRACT'
        inv_velvet.inputs[0].default_value = 1.0
        link(links, velvet_facing.outputs["Facing"], inv_velvet.inputs[1])
        
        pow_velvet = make_node(nodes, "ShaderNodeMath", "Velvet Power Falloff", (loc[0] + 420, loc[1] - 300))
        pow_velvet.operation = 'POWER'
        pow_velvet.inputs[1].default_value = velvet_power
        link(links, inv_velvet.outputs[0], pow_velvet.inputs[0])
        
        scale_velvet = make_node(nodes, "ShaderNodeMath", "Scale Velvet Sheen", (loc[0] + 580, loc[1] - 300))
        scale_velvet.operation = 'MULTIPLY'
        scale_velvet.inputs[1].default_value = velvet_sheen_str
        link(links, pow_velvet.outputs[0], scale_velvet.inputs[0])
        
        velvet_gated = make_node(nodes, "ShaderNodeMath", "Gate Velvet by Light", (loc[0] + 740, loc[1] - 300))
        velvet_gated.operation = 'MULTIPLY'
        link(links, scale_velvet.outputs[0], velvet_gated.inputs[0])
        link(links, light_step_socket, velvet_gated.inputs[1])

        # 2. Crisp Anime Cel Glints (Satin / Leather / Bodysuit)
        core_step = make_node(nodes, "ShaderNodeMapRange", "Specular Sharp Core", (loc[0] + 200, loc[1]))
        configure_smooth_step(core_step, 0.68, 0.74)
        link(links, gbw_light_socket, core_step.inputs[0])
        
        scale_cel = make_node(nodes, "ShaderNodeMath", "Scale Spec Core", (loc[0] + 400, loc[1]))
        scale_cel.operation = 'MULTIPLY'
        scale_cel.inputs[1].default_value = cloth_core_str * core_strength
        link(links, core_step.outputs[0], scale_cel.inputs[0])

        if cloth_mode == 'ANIME_MATTE':
            spec_with_features = velvet_gated.outputs[0]
        elif cloth_mode == 'CRISP_CEL':
            spec_with_features = scale_cel.outputs[0]
        elif cloth_mode == 'METALLIC' or is_metallic:
            comb_metal = make_node(nodes, "ShaderNodeMath", "Combine Metal Spec", (loc[0] + 600, loc[1]))
            comb_metal.operation = 'MAXIMUM'
            link(links, scale_cel.outputs[0], comb_metal.inputs[0])
            link(links, gbw_light_socket, comb_metal.inputs[1])
            spec_with_features = comb_metal.outputs[0]
        else:
            # HYBRID_AUTO: Smoothly decodes material based on ILM.B
            # Low ILM.B (< 0.25) = Matte fabric velvet sheen
            # High ILM.B (>= 0.25) = Crisp cel glints on belts/leather/metals
            mix_cloth = make_node(nodes, "ShaderNodeMix", "Hybrid Cloth Material Blend", (loc[0] + 900, loc[1]))
            mix_cloth.data_type = 'FLOAT'
            mix_cloth.blend_type = 'MIX'
            link(links, ilm_spec_bw_socket, find_socket(mix_cloth.inputs, "Factor", "Fac"))
            link(links, velvet_gated.outputs[0], find_socket(mix_cloth.inputs, "A", "Color1"))
            link(links, scale_cel.outputs[0], find_socket(mix_cloth.inputs, "B", "Color2"))
            spec_with_features = find_socket(mix_cloth.outputs, "Result", "Color")

    if bool(getattr(scene, "genos_use_vertex_fx_mask", True)):
        v_attr_spec = make_node(nodes, "ShaderNodeAttribute", "Vertex FX Attribute (Specular)", (loc[0] + 700, loc[1] - 150))
        v_attr_spec.attribute_name = "Anime_FX"
        v_attr_spec.attribute_type = 'GEOMETRY'
        sep_col_spec = make_node(nodes, "ShaderNodeSeparateColor", "Separate Vertex FX (Specular)", (loc[0] + 850, loc[1] - 150))
        link(links, v_attr_spec.outputs["Color"], sep_col_spec.inputs[0])
        add_vfx_spec = make_node(nodes, "ShaderNodeMath", "Add Vertex Specular", (loc[0] + 900, loc[1] - 150))
        add_vfx_spec.operation = 'ADD'
        link(links, ilm_spec_bw_socket, add_vfx_spec.inputs[0])
        link(links, sep_col_spec.outputs[1], add_vfx_spec.inputs[1])
        active_spec_bw = add_vfx_spec.outputs[0]
    else:
        active_spec_bw = ilm_spec_bw_socket

    # If shader is HAIR, allow procedural halo even if ILM_Spec is 0
    if shader_type == 'HAIR':
        allow_halo = make_node(nodes, "ShaderNodeMath", "Allow Hair Halo", (loc[0] + 1600, loc[1] - 300))
        allow_halo.operation = 'MAXIMUM'
        link(links, active_spec_bw, allow_halo.inputs[0])
        allow_halo.inputs[1].default_value = 0.85
        
        masked_spec = make_node(nodes, "ShaderNodeMath", "Mask Spec with ILM.B", (loc[0] + 1750, loc[1]))
        masked_spec.operation = 'MULTIPLY'
        link(links, spec_with_features, masked_spec.inputs[0])
        link(links, allow_halo.outputs[0], masked_spec.inputs[1])
    else:
        # For clothing / default, if hybrid auto or matte, velvet is already modulated
        masked_spec = make_node(nodes, "ShaderNodeMath", "Mask Spec with ILM.B", (loc[0] + 1050, loc[1]))
        masked_spec.operation = 'MULTIPLY'
        link(links, spec_with_features, masked_spec.inputs[0])
        if cloth_mode == 'ANIME_MATTE':
            masked_spec.inputs[1].default_value = 1.0
        else:
            allow_cloth = make_node(nodes, "ShaderNodeMath", "Allow Cloth Base Sheen", (loc[0] + 1000, loc[1] - 150))
            allow_cloth.operation = 'MAXIMUM'
            link(links, active_spec_bw, allow_cloth.inputs[0])
            allow_cloth.inputs[1].default_value = 0.70
            link(links, allow_cloth.outputs[0], masked_spec.inputs[1])

    # Optional standard roughness/metallic maps modulate the stylized spec response.
    # This keeps the anime shape language while respecting authored PBR data.
    if roughness_socket is not None:
        inv_rough = make_node(nodes, 'ShaderNodeMath', 'Invert Roughness Map', (loc[0] + 1500, loc[1] + 170))
        inv_rough.operation = 'SUBTRACT'; inv_rough.inputs[0].default_value = 1.0
        link(links, roughness_socket, inv_rough.inputs[1])
        rough_scale = make_node(nodes, 'ShaderNodeMath', 'Roughness Spec Scale', (loc[0] + 1660, loc[1] + 170))
        rough_scale.operation = 'MULTIPLY'; rough_scale.inputs[1].default_value = 0.85
        link(links, inv_rough.outputs[0], rough_scale.inputs[0])
        rough_floor = make_node(nodes, 'ShaderNodeMath', 'Roughness Spec Floor', (loc[0] + 1820, loc[1] + 170))
        rough_floor.operation = 'ADD'; rough_floor.inputs[1].default_value = 0.15
        link(links, rough_scale.outputs[0], rough_floor.inputs[0])
    else:
        rough_floor = make_node(nodes, 'ShaderNodeValue', 'Roughness Spec Floor', (loc[0] + 1820, loc[1] + 170))
        rough_floor.outputs[0].default_value = 1.0

    if metallic_socket is not None:
        metal_scale = make_node(nodes, 'ShaderNodeMath', 'Metallic Spec Boost', (loc[0] + 1660, loc[1] + 280))
        metal_scale.operation = 'MULTIPLY'; metal_scale.inputs[1].default_value = 0.35
        link(links, metallic_socket, metal_scale.inputs[0])
        metal_add = make_node(nodes, 'ShaderNodeMath', 'Metallic Spec Base', (loc[0] + 1820, loc[1] + 280))
        metal_add.operation = 'ADD'; metal_add.inputs[1].default_value = 1.0
        link(links, metal_scale.outputs[0], metal_add.inputs[0])
        map_scale = make_node(nodes, 'ShaderNodeMath', 'PBR Map Spec Scale', (loc[0] + 1980, loc[1] + 220))
        map_scale.operation = 'MULTIPLY'
        link(links, rough_floor.outputs[0], map_scale.inputs[0]); link(links, metal_add.outputs[0], map_scale.inputs[1])
    else:
        map_scale = rough_floor

    mapped_spec = make_node(nodes, 'ShaderNodeMath', 'Apply PBR Map Spec Scale', (loc[0] + 2140, loc[1] + 130))
    mapped_spec.operation = 'MULTIPLY'
    link(links, masked_spec.outputs[0], mapped_spec.inputs[0]); link(links, map_scale.outputs[0], mapped_spec.inputs[1])
    masked_spec = mapped_spec

    if not is_metallic:
        light_gated_spec = make_node(nodes, "ShaderNodeMath", "Light Mask Specular", (loc[0] + 2300, loc[1]))
        light_gated_spec.operation = 'MULTIPLY'
        link(links, masked_spec.outputs[0], light_gated_spec.inputs[0])
        link(links, light_step_socket, light_gated_spec.inputs[1])
        final_spec_mask = light_gated_spec.outputs[0]
    else:
        final_spec_mask = masked_spec.outputs[0]

    spec_clamp = make_node(nodes, "ShaderNodeClamp", "Final Spec Clamp", (loc[0] + 2050, loc[1]))
    link(links, final_spec_mask, spec_clamp.inputs[0])
    try:
        spec_clamp.inputs[1].default_value = 0.0
        spec_clamp.inputs[2].default_value = 1.0
    except Exception:
        pass

    spec_tint_mix = make_node(nodes, "ShaderNodeMix", "Specular Tint", (loc[0] + 2200, loc[1]))
    spec_tint_mix.data_type = 'RGBA'
    spec_tint_mix.blend_type = 'MIX'
    link(links, spec_clamp.outputs[0], find_socket(spec_tint_mix.inputs, "Factor", "Fac"))
    find_socket(spec_tint_mix.inputs, "A", "Color1").default_value = (0.0, 0.0, 0.0, 1.0)
    
    if is_metallic or (cloth_mode == 'METALLIC' and shader_type != 'HAIR'):
        link(links, base_color_socket, find_socket(spec_tint_mix.inputs, "B", "Color2"))
    elif shader_type == 'HAIR':
        # Core glints keep a general tint, while halo bands/spots use their own live colors.
        h_tint = tuple(getattr(scene, "genos_hair_highlight_tint", (1.0, 0.82, 0.88, 1.0)))
        hair_tint = make_node(nodes, "ShaderNodeMix", "Hair Highlight Tint", (loc[0] + 2100, loc[1] - 200))
        hair_tint.data_type = 'RGBA'
        hair_tint.blend_type = 'MIX'
        find_socket(hair_tint.inputs, "Factor", "Fac").default_value = 0.65
        link(links, base_color_socket, find_socket(hair_tint.inputs, "A", "Color1"))
        find_socket(hair_tint.inputs, "B", "Color2").default_value = h_tint

        halo_color_mix = make_node(nodes, "ShaderNodeMix", "Hair Halo Color Blend", (loc[0] + 2260, loc[1] - 220))
        halo_color_mix.data_type = 'RGBA'
        halo_color_mix.blend_type = 'MIX'
        link(links, clean_halos, find_socket(halo_color_mix.inputs, "Factor", "Fac"))
        link(links, find_socket(hair_tint.outputs, "Result", "Color"), find_socket(halo_color_mix.inputs, "A", "Color1"))
        link(links, clean_halo_color, find_socket(halo_color_mix.inputs, "B", "Color2"))
        link(links, find_socket(halo_color_mix.outputs, "Result", "Color"), find_socket(spec_tint_mix.inputs, "B", "Color2"))
    else:
        # Clothing: soft illustrative highlight blend (base color mixed with light tint)
        # Prevents cold plastic white reflections over dyed fabric
        cloth_tint = make_node(nodes, "ShaderNodeMix", "Cloth Highlight Glaze", (loc[0] + 2100, loc[1] - 200))
        cloth_tint.data_type = 'RGBA'
        cloth_tint.blend_type = 'MIX'
        find_socket(cloth_tint.inputs, "Factor", "Fac").default_value = 0.50
        link(links, base_color_socket, find_socket(cloth_tint.inputs, "A", "Color1"))
        find_socket(cloth_tint.inputs, "B", "Color2").default_value = tuple(getattr(scene, "genos_spec_tint", (1.0, 1.0, 1.0, 1.0)))
        link(links, find_socket(cloth_tint.outputs, "Result", "Color"), find_socket(spec_tint_mix.inputs, "B", "Color2"))

    return find_socket(spec_tint_mix.outputs, "Result", "Color"), spec_clamp.outputs[0]

def _build_directional_rim_system(nodes, links, scene, normal_socket, ilm_rim_socket, light_step_socket, loc=(1400, -800)):
    """
    Directional Silhouette Rim Light (Backlight & Tuft Highlight):
    - Fresnel / LayerWeight edge falloff with customizable power curve
    - Directional Backlight Modulation: concentrates rim onto backlit/silhouette side
    - Colored by customizable rim color & intensity
    """
    layer_weight = make_node(nodes, "ShaderNodeLayerWeight", "Rim Layer Weight", (loc[0], loc[1]))
    layer_weight.inputs["Blend"].default_value = 0.2
    link(links, normal_socket, layer_weight.inputs["Normal"])
    
    inv_facing = make_node(nodes, "ShaderNodeMath", "Invert Facing for Rim", (loc[0] + 180, loc[1]))
    inv_facing.operation = 'SUBTRACT'
    inv_facing.inputs[0].default_value = 1.0
    link(links, layer_weight.outputs["Facing"], inv_facing.inputs[1])
    
    power_node = make_node(nodes, "ShaderNodeMath", "Rim Power Curve", (loc[0] + 340, loc[1]))
    power_node.operation = 'POWER'
    link(links, inv_facing.outputs[0], power_node.inputs[0])
    power_node.inputs[1].default_value = float(getattr(scene, "genos_rim_power", 2.5))
    
    if bool(getattr(scene, "genos_rim_directional", True)):
        backlight = make_node(nodes, "ShaderNodeMath", "Rim Backlight Side", (loc[0] + 340, loc[1] - 150))
        backlight.operation = 'SUBTRACT'
        backlight.inputs[0].default_value = 1.0
        link(links, light_step_socket, backlight.inputs[1])
        
        bias_scale = make_node(nodes, "ShaderNodeMath", "Rim Backlight Scale", (loc[0] + 500, loc[1] - 150))
        bias_scale.operation = 'MULTIPLY'
        link(links, backlight.outputs[0], bias_scale.inputs[0])
        bias_scale.inputs[1].default_value = 0.65
        
        bias_add = make_node(nodes, "ShaderNodeMath", "Rim Backlight Bias", (loc[0] + 660, loc[1] - 150))
        bias_add.operation = 'ADD'
        link(links, bias_scale.outputs[0], bias_add.inputs[0])
        bias_add.inputs[1].default_value = 0.35
        
        rim_mod = make_node(nodes, "ShaderNodeMath", "Directional Rim Modulation", (loc[0] + 820, loc[1]))
        rim_mod.operation = 'MULTIPLY'
        link(links, power_node.outputs[0], rim_mod.inputs[0])
        link(links, bias_add.outputs[0], rim_mod.inputs[1])
        base_rim = rim_mod.outputs[0]
    else:
        base_rim = power_node.outputs[0]
        
    if bool(getattr(scene, "genos_use_vertex_fx_mask", True)):
        v_attr_rim = make_node(nodes, "ShaderNodeAttribute", "Vertex FX Attribute (Rim)", (loc[0] + 700, loc[1] - 350))
        v_attr_rim.attribute_name = "Anime_FX"
        v_attr_rim.attribute_type = 'GEOMETRY'
        sep_col_rim = make_node(nodes, "ShaderNodeSeparateColor", "Separate Vertex FX (Rim)", (loc[0] + 850, loc[1] - 350))
        link(links, v_attr_rim.outputs["Color"], sep_col_rim.inputs[0])
        add_vfx_rim = make_node(nodes, "ShaderNodeMath", "Add Vertex Rim", (loc[0] + 980, loc[1] - 200))
        add_vfx_rim.operation = 'ADD'
        link(links, ilm_rim_socket, add_vfx_rim.inputs[0])
        link(links, sep_col_rim.outputs[2], add_vfx_rim.inputs[1])
        active_rim_socket = add_vfx_rim.outputs[0]
    else:
        active_rim_socket = ilm_rim_socket

    rim_mask = make_node(nodes, "ShaderNodeMath", "Mask Rim with ILM.A", (loc[0] + 980, loc[1]))
    rim_mask.operation = 'MULTIPLY'
    link(links, base_rim, rim_mask.inputs[0])
    link(links, active_rim_socket, rim_mask.inputs[1])
    
    rim_gain = make_node(nodes, "ShaderNodeMath", "Scale Rim Intensity", (loc[0] + 1140, loc[1]))
    rim_gain.operation = 'MULTIPLY'
    link(links, rim_mask.outputs[0], rim_gain.inputs[0])
    rim_gain.inputs[1].default_value = float(getattr(scene, "genos_rim_intensity", 1.0))
    
    rim_step = make_node(nodes, "ShaderNodeMapRange", "Smooth Rim Clamp", (loc[0] + 1300, loc[1]))
    configure_smooth_step(rim_step, 0.15, 0.85)
    link(links, rim_gain.outputs[0], rim_step.inputs[0])

    rim_color_node = make_node(nodes, "ShaderNodeMix", "Colorize Rim Light", (loc[0] + 1480, loc[1]))
    rim_color_node.data_type = 'RGBA'
    rim_color_node.blend_type = 'MIX'
    link(links, rim_step.outputs[0], find_socket(rim_color_node.inputs, "Factor", "Fac"))
    find_socket(rim_color_node.inputs, "A", "Color1").default_value = (0.0, 0.0, 0.0, 1.0)
    find_socket(rim_color_node.inputs, "B", "Color2").default_value = tuple(getattr(scene, "genos_rim_color", (0.90, 0.94, 1.0, 1.0)))
    
    return find_socket(rim_color_node.outputs, "Result", "Color"), rim_step.outputs[0]

def _build_anime_emission_system(nodes, links, scene, emission_source_socket, ilm_emit_socket, det_emit_socket, pattern_base_socket, shadow_step_socket, hair_band_emit_mask_socket=None, hair_band_emit_color_socket=None, loc=(1000, -350)):
    """
    Advanced Emission System (Sci-Fi Tech Glow, Overdrive & Pulse):
    - Scaled custom emission map
    - Color-tinted ILM/Detail mask glow
    - Optional Sci-Fi Breathing Pulse
    - Unlit shadow cut-through or shadowed blend
    """
    map_strength = make_node(nodes, "ShaderNodeValue", "Emission Map Strength", (loc[0], loc[1]))
    map_strength.outputs[0].default_value = getattr(scene, 'genos_emission_map_strength', 1.0)

    map_strength_mul = make_node(nodes, "ShaderNodeMix", "Scale Emission Map", (loc[0] + 200, loc[1]))
    map_strength_mul.data_type = 'RGBA'
    map_strength_mul.blend_type = 'MULTIPLY'
    find_socket(map_strength_mul.inputs, "Factor", "Fac").default_value = 1.0
    link(links, emission_source_socket, find_socket(map_strength_mul.inputs, "A", "Color1"))
    link(links, map_strength.outputs[0], find_socket(map_strength_mul.inputs, "B", "Color2"))

    # Procedural hair highlight/angel-ring emission is merged into the same
    # logical ILM.G emission channel before Detail.A and vertex FX are added.
    ilm_active_socket = ilm_emit_socket
    if hair_band_emit_mask_socket is not None:
        hair_ilm = make_node(nodes, "ShaderNodeMath", "ILM Emission + Hair Bands", (loc[0] - 180, loc[1] - 150))
        hair_ilm.operation = 'MAXIMUM'
        link(links, ilm_emit_socket, hair_ilm.inputs[0])
        link(links, hair_band_emit_mask_socket, hair_ilm.inputs[1])
        ilm_active_socket = hair_ilm.outputs[0]

    combo_emit = make_node(nodes, "ShaderNodeMath", "Combine Glow Masks", (loc[0], loc[1] - 150))
    combo_emit.operation = 'ADD'
    link(links, ilm_active_socket, combo_emit.inputs[0])
    link(links, det_emit_socket, combo_emit.inputs[1])

    if bool(getattr(scene, "genos_use_vertex_fx_mask", True)):
        v_attr_emit = make_node(nodes, "ShaderNodeAttribute", "Vertex FX Attribute (Emission)", (loc[0] - 250, loc[1] - 300))
        v_attr_emit.attribute_name = "Anime_FX"
        v_attr_emit.attribute_type = 'GEOMETRY'
        sep_col_emit = make_node(nodes, "ShaderNodeSeparateColor", "Separate Vertex FX (Emission)", (loc[0] - 100, loc[1] - 300))
        link(links, v_attr_emit.outputs["Color"], sep_col_emit.inputs[0])
        add_vfx_glow = make_node(nodes, "ShaderNodeMath", "Add Vertex Glow", (loc[0] + 100, loc[1] - 150))
        add_vfx_glow.operation = 'ADD'
        link(links, combo_emit.outputs[0], add_vfx_glow.inputs[0])
        link(links, sep_col_emit.outputs[0], add_vfx_glow.inputs[1])
        active_glow_mask = add_vfx_glow.outputs[0]
    else:
        active_glow_mask = combo_emit.outputs[0]

    glow_clamp = make_node(nodes, 'ShaderNodeClamp', 'Bound Glow Mask', (loc[0] + 100, loc[1] - 300))
    link(links, active_glow_mask, glow_clamp.inputs[0])
    active_glow_mask = glow_clamp.outputs[0]
    mask_base_glow = make_node(nodes, "ShaderNodeMix", "Masked Base Glow", (loc[0] + 250, loc[1] - 150))
    mask_base_glow.data_type = 'RGBA'
    mask_base_glow.blend_type = 'MULTIPLY'
    find_socket(mask_base_glow.inputs, "Factor", "Fac").default_value = 1.0
    link(links, pattern_base_socket, find_socket(mask_base_glow.inputs, "A", "Color1"))
    link(links, active_glow_mask, find_socket(mask_base_glow.inputs, "B", "Color2"))

    tinted_mask_glow = make_node(nodes, "ShaderNodeMix", "Tint Mask Glow", (loc[0] + 400, loc[1] - 150))
    tinted_mask_glow.data_type = 'RGBA'
    tinted_mask_glow.blend_type = 'MULTIPLY'
    find_socket(tinted_mask_glow.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(mask_base_glow.outputs, "Result", "Color"), find_socket(tinted_mask_glow.inputs, "A", "Color1"))
    find_socket(tinted_mask_glow.inputs, "B", "Color2").default_value = tuple(getattr(scene, "genos_emission_tint", (1.0, 0.45, 0.1, 1.0)))

    total_emission = make_node(nodes, "ShaderNodeMix", "Total Raw Emission", (loc[0] + 600, loc[1]))
    total_emission.data_type = 'RGBA'
    total_emission.blend_type = 'ADD'
    find_socket(total_emission.inputs, "Factor", "Fac").default_value = 1.0
    link(links, find_socket(tinted_mask_glow.outputs, "Result", "Color"), find_socket(total_emission.inputs, "A", "Color1"))
    link(links, find_socket(map_strength_mul.outputs, "Result", "Color"), find_socket(total_emission.inputs, "B", "Color2"))
    
    current_emission = find_socket(total_emission.outputs, "Result", "Color")

    # Preserve each hair band's own color and arbitrary glow strength while the
    # mask above simultaneously participates in the ILM.G channel. This avoids
    # forcing pale/pink angel rings through the global orange/tech emission tint.
    if hair_band_emit_color_socket is not None:
        add_hair_emission = make_node(nodes, "ShaderNodeMix", "Add Hair Band Colored Emission", (loc[0] + 760, loc[1] + 120))
        add_hair_emission.data_type = 'RGBA'
        add_hair_emission.blend_type = 'ADD'
        find_socket(add_hair_emission.inputs, "Factor", "Fac").default_value = 1.0
        link(links, current_emission, find_socket(add_hair_emission.inputs, "A", "Color1"))
        link(links, hair_band_emit_color_socket, find_socket(add_hair_emission.inputs, "B", "Color2"))
        current_emission = find_socket(add_hair_emission.outputs, "Result", "Color")

    if bool(getattr(scene, "genos_emission_pulse_enable", False)):
        pulse_coord = make_node(nodes, "ShaderNodeTexCoord", "Pulse TexCoord", (loc[0] + 400, loc[1] - 300))
        pulse_sine = make_node(nodes, "ShaderNodeTexWave", "Pulse Wave Oscillation", (loc[0] + 580, loc[1] - 300))
        pulse_sine.wave_type = 'BANDS'
        pulse_sine.bands_direction = 'Z'
        pulse_sine.wave_profile = 'SIN'
        pulse_sine.inputs["Scale"].default_value = float(getattr(scene, "genos_emission_pulse_speed", 2.0))
        link(links, pulse_coord.outputs["Generated"], pulse_sine.inputs["Vector"])

        pulse_map = make_node(nodes, "ShaderNodeMapRange", "Pulse Remap", (loc[0] + 760, loc[1] - 300))
        pulse_map.inputs["From Min"].default_value = 0.0
        pulse_map.inputs["From Max"].default_value = 1.0
        pulse_map.inputs["To Min"].default_value = 0.65
        pulse_map.inputs["To Max"].default_value = 1.25
        link(links, pulse_sine.outputs["Color"], pulse_map.inputs[0])

        pulse_mul = make_node(nodes, "ShaderNodeMix", "Apply Emission Pulse", (loc[0] + 800, loc[1]))
        pulse_mul.data_type = 'RGBA'
        pulse_mul.blend_type = 'MULTIPLY'
        find_socket(pulse_mul.inputs, "Factor", "Fac").default_value = 1.0
        link(links, current_emission, find_socket(pulse_mul.inputs, "A", "Color1"))
        link(links, pulse_map.outputs[0], find_socket(pulse_mul.inputs, "B", "Color2"))
        current_emission = find_socket(pulse_mul.outputs, "Result", "Color")

    emit_shadow_toggle = make_node(nodes, "ShaderNodeValue", "Emission Receives Shadows", (loc[0] + 600, loc[1] - 450))
    emit_shadow_toggle.outputs[0].default_value = 0.0

    emit_shadow_mix = make_node(nodes, "ShaderNodeMix", "Shadowed Emission Blend", (loc[0] + 800, loc[1] - 450))
    emit_shadow_mix.data_type = 'FLOAT'
    link(links, emit_shadow_toggle.outputs[0], emit_shadow_mix.inputs[0])
    emit_shadow_mix.inputs[2].default_value = 1.0
    link(links, shadow_step_socket, emit_shadow_mix.inputs[3])

    emit_shadow_tint = make_node(nodes, "ShaderNodeMix", "Emissive Shadow Tint Color", (loc[0] + 800, loc[1] - 600))
    emit_shadow_tint.data_type = 'RGBA'
    emit_shadow_tint.blend_type = 'MULTIPLY'
    find_socket(emit_shadow_tint.inputs, "Factor", "Fac").default_value = 1.0
    link(links, current_emission, find_socket(emit_shadow_tint.inputs, "A", "Color1"))
    find_socket(emit_shadow_tint.inputs, "B", "Color2").default_value = (0.2, 0.05, 0.5, 1.0)

    shade_emission_final = make_node(nodes, "ShaderNodeMix", "Apply Shadowed Emission", (loc[0] + 1000, loc[1]))
    shade_emission_final.data_type = 'RGBA'
    shade_emission_final.blend_type = 'MIX'
    link(links, emit_shadow_mix.outputs[0], find_socket(shade_emission_final.inputs, "Factor", "Fac"))
    link(links, find_socket(emit_shadow_tint.outputs, "Result", "Color"), find_socket(shade_emission_final.inputs, "A", "Color1"))
    link(links, current_emission, find_socket(shade_emission_final.inputs, "B", "Color2"))

    return find_socket(shade_emission_final.outputs, "Result", "Color")

# -------------------------------------------------------------------
# Scene & Material Properties
# -------------------------------------------------------------------

def _update_surface_map_live(scene, context):
    """Update scalar normal/displacement controls without rebuilding materials."""
    try:
        materials = {slot.material for obj in scene.objects for slot in obj.material_slots if slot.material and slot.material.use_nodes}
    except Exception:
        materials = set()
    for mat in materials:
        nodes = mat.node_tree.nodes
        normal = nodes.get('Normal Map')
        if normal and normal.inputs.get('Strength'):
            normal.inputs['Strength'].default_value = float(getattr(scene, 'genos_normal_strength', 1.0))
        bump = nodes.get('Displacement Bump')
        if bump:
            if bump.inputs.get('Distance'):
                bump.inputs['Distance'].default_value = float(getattr(scene, 'genos_displacement_strength', 0.1))
            if bump.inputs.get('Midlevel'):
                bump.inputs['Midlevel'].default_value = float(getattr(scene, 'genos_displacement_midlevel', 0.5))
        disp = nodes.get('True Displacement')
        if disp:
            if disp.inputs.get('Scale'):
                disp.inputs['Scale'].default_value = float(getattr(scene, 'genos_displacement_strength', 0.1))
            if disp.inputs.get('Midlevel'):
                disp.inputs['Midlevel'].default_value = float(getattr(scene, 'genos_displacement_midlevel', 0.5))
        try: mat.node_tree.update_tag()
        except Exception: pass


def _rebuild_surface_map_mode(scene, context):
    """Rebuild only toon materials when node topology must change (DX normals/true displacement)."""
    if bool(scene.get('_genos_surface_rebuild_guard', False)):
        return
    try:
        scene['_genos_surface_rebuild_guard'] = True
        materials = {slot.material for obj in scene.objects for slot in obj.material_slots if slot.material and slot.material.use_nodes and (slot.material.get('is_anime_toon') or slot.material.get('is_anime_toon_baked'))}
        for mat in materials:
            try:
                adapt_material_to_new_scheme(mat)
            except Exception as exc:
                print('[Anime Studio] Surface map topology rebuild failed for %s: %s' % (mat.name, exc))
    finally:
        try: scene['_genos_surface_rebuild_guard'] = False
        except Exception: pass


def _ensure_optional_map_topology(mat, node_name, img):
    """Rebuild a toon material only when adding/removing an optional texture changes topology."""
    if not mat or not getattr(mat, 'use_nodes', False) or not getattr(mat, 'node_tree', None):
        return
    if not (mat.get('is_anime_toon') or mat.get('is_anime_toon_baked')):
        return
    node = mat.node_tree.nodes.get(node_name)
    needs = (is_valid_image(img) and node is None) or ((not is_valid_image(img)) and node is not None)
    if not needs or bool(mat.get('_genos_map_rebuild_guard', False)):
        return
    try:
        mat['_genos_map_rebuild_guard'] = True
        adapt_material_to_new_scheme(mat)
    except Exception as exc:
        print('[Anime Studio] Optional map topology rebuild failed for %s: %s' % (mat.name, exc))
    finally:
        try: mat['_genos_map_rebuild_guard'] = False
        except Exception: pass


def _update_mat_basecolor(self, context):
    if hasattr(self, "genos_base_color_map") and self.genos_base_color_map:
        try:
            set_image_colorspace(self.genos_base_color_map, 'sRGB')
            self.genos_base_color_map.use_fake_user = True
        except Exception:
            pass
    if hasattr(self, "node_tree") and self.node_tree:
        node = self.node_tree.nodes.get("BaseColor")
        if node and node.image != self.genos_base_color_map:
            node.image = self.genos_base_color_map

def _update_mat_emission(self, context):
    if hasattr(self, "genos_emission_map") and self.genos_emission_map:
        try:
            set_image_colorspace(self.genos_emission_map, 'sRGB')
            self.genos_emission_map.use_fake_user = True
        except Exception:
            pass
    if hasattr(self, "node_tree") and self.node_tree:
        node = self.node_tree.nodes.get("Emission Map")
        if node and node.image != self.genos_emission_map:
            node.image = self.genos_emission_map

def _update_mat_normal(self, context):
    if hasattr(self, "genos_normal_map") and self.genos_normal_map:
        try:
            set_image_colorspace(self.genos_normal_map, 'Non-Color')
            self.genos_normal_map.use_fake_user = True
        except Exception:
            pass
    if hasattr(self, "node_tree") and self.node_tree:
        node = self.node_tree.nodes.get("Normal_Tex")
        if node and node.image != self.genos_normal_map:
            node.image = self.genos_normal_map
    _ensure_optional_map_topology(self, 'Normal_Tex', getattr(self, 'genos_normal_map', None))

def _update_mat_displacement(self, context):
    if hasattr(self, "genos_displacement_map") and self.genos_displacement_map:
        try:
            set_image_colorspace(self.genos_displacement_map, 'Non-Color')
            self.genos_displacement_map.use_fake_user = True
        except Exception:
            pass
    if hasattr(self, "node_tree") and self.node_tree:
        node = self.node_tree.nodes.get("Displacement Map")
        if node and node.image != self.genos_displacement_map:
            node.image = self.genos_displacement_map

def _update_mat_roughness(self, context):
    img = getattr(self, "genos_roughness_map", None)
    if img:
        configure_standard_map_image(img, 'ROUGHNESS')
    if getattr(self, 'node_tree', None):
        node = self.node_tree.nodes.get('Roughness Map')
        if node and getattr(node, 'image', None) != img:
            node.image = img
    _ensure_optional_map_topology(self, 'Roughness Map', img)


def _update_mat_metallic(self, context):
    img = getattr(self, "genos_metallic_map", None)
    if img:
        configure_standard_map_image(img, 'METALLIC')
    if getattr(self, 'node_tree', None):
        node = self.node_tree.nodes.get('Metallic Map')
        if node and getattr(node, 'image', None) != img:
            node.image = img
    _ensure_optional_map_topology(self, 'Metallic Map', img)


def _update_mat_opacity(self, context):
    img = getattr(self, "genos_opacity_map", None)
    if img:
        configure_standard_map_image(img, 'OPACITY')
    if getattr(self, 'node_tree', None):
        node = self.node_tree.nodes.get('Opacity Map')
        if node and getattr(node, 'image', None) != img:
            node.image = img
    _ensure_optional_map_topology(self, 'Opacity Map', img)


def _update_mat_ao(self, context):
    img = getattr(self, "genos_ao_map", None)
    if img:
        configure_standard_map_image(img, 'AO')
    if getattr(self, 'node_tree', None):
        node = self.node_tree.nodes.get('AO Map')
        if node and getattr(node, 'image', None) != img:
            node.image = img
    _ensure_optional_map_topology(self, 'AO Map', img)


def _update_mat_sdf(self, context):
    if hasattr(self, "genos_sdf_map") and self.genos_sdf_map:
        try:
            set_image_colorspace(self.genos_sdf_map, 'Non-Color')
            self.genos_sdf_map.use_fake_user = True
        except Exception:
            pass
    if hasattr(self, "node_tree") and self.node_tree:
        node = self.node_tree.nodes.get("SDF Map")
        if node and node.image != self.genos_sdf_map:
            node.image = self.genos_sdf_map

def image_prop(name, update=None):
    if update:
        return PointerProperty(name=name, type=bpy.types.Image, update=update)
    return PointerProperty(name=name, type=bpy.types.Image)

def _update_hair_halo_live(scene, context):
    """Patch dynamic highlight bands in realtime without rebuilding their structure."""
    if globals().get('_GENOS_HAIR_BAND_BATCH_UPDATE', False):
        return
    specs = _hair_band_specs(scene)
    materials = {slot.material for obj in scene.objects for slot in obj.material_slots
                 if slot.material and slot.material.use_nodes}
    for mat in materials:
        nodes = mat.node_tree.nodes
        if nodes.get('Hair Facing Weight') is None:
            continue

        def value(name, socket, amount):
            node = nodes.get(name)
            if node is not None:
                try: node.inputs[socket].default_value = amount
                except Exception: pass

        value('Scale Angel Ring', 1, scene.genos_hair_highlight_strength)
        value('Modulate Strands Scale', 1, scene.genos_hair_strands_strength)
        value('Strands Bias', 1, max(0.2, 1.0 - scene.genos_hair_strands_strength * 0.7))
        value('Hair Strand Cuts', 'Scale', scene.genos_hair_strands_scale)
        value('Scale Peak Glint', 1, scene.genos_spec_core_strength * 0.4 * scene.genos_hair_pbr_spec_str)
        tint = nodes.get('Hair Highlight Tint')
        if tint: find_socket(tint.inputs, 'B').default_value = scene.genos_hair_highlight_tint

        gen_sep = nodes.get('Hair Bands Generated XYZ')
        uv_sep = nodes.get('Hair Bands UV XYZ')
        obj_sep = nodes.get('Hair Bands Object XYZ')
        norm_sep = nodes.get('Hair Bands Normal XYZ')

        for zero_index, spec in enumerate(specs):
            i = zero_index + 1
            prefix = 'Clean Halo %d ' % i
            mode = spec['coord_mode']
            if mode == 'UV_V' and uv_sep is not None:
                elev = uv_sep.outputs['Y']
            elif mode == 'NORMAL_Z' and norm_sep is not None:
                elev = norm_sep.outputs['Z']
            elif mode == 'OBJECT_Z' and obj_sep is not None:
                elev = obj_sep.outputs['Z']
            elif gen_sep is not None:
                elev = gen_sep.outputs['Z']
            else:
                elev = None

            curved = nodes.get(prefix + 'Curved Elevation')
            if curved is not None:
                curved.inputs[1].default_value = spec['curvature'] * 4.0
                if elev is not None and (not curved.inputs[2].links or curved.inputs[2].links[0].from_socket != elev):
                    link(mat.node_tree.links, elev, curved.inputs[2])

            value(prefix + 'Curve Center', 1, spec['curve_center'])
            value(prefix + 'Position', 1, spec['position'])
            value(prefix + 'Spot Density', 1, spec['spot_density'])
            value(prefix + 'Visible Gain', 1, (spec['strength'] * spec['opacity']) if spec['enabled'] else 0.0)
            value(prefix + 'Emission Enabled', 1, 1.0 if (spec['enabled'] and spec['emission_enabled']) else 0.0)
            value(prefix + 'Emission Strength', 1, spec['emission_strength'] if (spec['enabled'] and spec['emission_enabled']) else 0.0)

            edge = nodes.get(prefix + 'Edge')
            if edge is not None:
                inner = max(0.0, spec['width'] * (1.0 - spec['softness'] - spec['blur'] * 0.85))
                outer = spec['width'] * (1.0 + spec['blur'] * 1.35)
                configure_smooth_step(edge, inner, outer, 1.0, 0.0)

            half_width = max(0.02, 0.50 - spec['spot_gap'] * 0.48)
            ribbon = nodes.get(prefix + 'Ribbon Cell')
            if ribbon is not None:
                rf = 0.015 + spec['blur'] * 0.18
                configure_smooth_step(ribbon, max(0.0, half_width - rf), half_width + rf,
                                      1.0, min(0.55, spec['blur'] * 0.35))

            aspect = 1.0 if spec['spot_shape'] == 'ROUND' else spec['spot_aspect']
            value(prefix + 'Circle X Radius', 1, max(0.035, half_width * aspect))
            value(prefix + 'Circle Y Radius', 1, spec['width'])
            circle = nodes.get(prefix + 'Circle Edge')
            if circle is not None:
                cf = 0.025 + spec['softness'] * 0.20 + spec['blur'] * 0.40
                configure_smooth_step(circle, max(0.0, 1.0 - cf), 1.0 + cf, 1.0, 0.0)

            shape = nodes.get(prefix + 'Spot Shape')
            if shape is not None:
                find_socket(shape.inputs, 'Factor').default_value = 0.0 if spec['spot_shape'] == 'RIBBON' else 1.0
            strand = nodes.get(prefix + 'Strand Detail')
            if strand is not None:
                find_socket(strand.inputs, 'Factor').default_value = spec['strand_detail'] * (1.0 - spec['blur'] * 0.85)
            color = nodes.get(prefix + 'Color')
            if color is not None: color.outputs[0].default_value = spec['color']

        if nodes.get('Preserve Painted Hair'):
            find_socket(nodes['Preserve Painted Hair'].inputs, 'Factor').default_value = scene.genos_hair_source_preservation
        _update_hair_toon_live(scene, context, only_mat=mat)
        try: mat.node_tree.update_tag()
        except Exception: pass

def _update_dynamic_hair_band_item(self, context):
    if globals().get('_GENOS_HAIR_BAND_BATCH_UPDATE', False): return
    scene = getattr(context, 'scene', None) if context else None
    if scene is not None: _update_hair_halo_live(scene, context)


def _update_dynamic_hair_toon_item(self, context):
    if globals().get('_GENOS_HAIR_TOON_BATCH_UPDATE', False): return
    scene = getattr(context, 'scene', None) if context else None
    if scene is not None: _update_hair_toon_live(scene, context)


class GENOS_HairBandItem(bpy.types.PropertyGroup):
    """One completely independent realtime highlight band."""
    label: StringProperty(name='Name', default='Band')
    enabled: BoolProperty(name='Enabled', default=True, update=_update_dynamic_hair_band_item)
    coord_mode: EnumProperty(name='Elevation', items=[
        ('GENERATED_Z', 'Generated Z', 'Generated coordinate height'),
        ('OBJECT_Z', 'Object Z', 'Object-space height'),
        ('UV_V', 'UV V', 'UV vertical coordinate'),
        ('NORMAL_Z', 'Normal Z', 'Surface normal vertical component'),
    ], default='GENERATED_Z', update=_update_dynamic_hair_band_item)
    position: FloatProperty(name='Position', default=0.5, min=-100.0, max=100.0, soft_min=0.0, soft_max=1.0, precision=3, update=_update_dynamic_hair_band_item)
    width: FloatProperty(name='Width', default=0.05, min=0.001, max=1.0, precision=3, update=_update_dynamic_hair_band_item)
    strength: FloatProperty(name='Strength', default=1.0, min=0.0, max=2.0, precision=3, update=_update_dynamic_hair_band_item)
    opacity: FloatProperty(name='Highlight Opacity', default=1.0, min=0.0, max=1.0, precision=3, update=_update_dynamic_hair_band_item, description='Transparency of this highlight/angel-ring band only; also fades its glow')
    emission_enabled: BoolProperty(name='Emit to ILM.G', default=True, update=_update_dynamic_hair_band_item, description='Feed this band into the ILM emission/glow pipeline')
    emission_strength: FloatProperty(name='Glow Strength', default=0.60, min=0.0, max=20.0, soft_max=5.0, precision=3, update=_update_dynamic_hair_band_item, description='Independent emissive glow intensity for this band; can exceed 1 for bloom')
    color: FloatVectorProperty(name='Band Color', subtype='COLOR', size=4, min=0.0, max=1.0, default=(1.0, 0.95, 0.98, 1.0), update=_update_dynamic_hair_band_item)
    softness: FloatProperty(name='Edge Softness', default=0.22, min=0.0, max=1.0, precision=3, update=_update_dynamic_hair_band_item)
    blur: FloatProperty(name='Blur / Feather', default=0.18, min=0.0, max=1.0, precision=3, update=_update_dynamic_hair_band_item)
    curve_center: FloatProperty(name='Curve Center', default=0.50, min=-2.0, max=2.0, soft_min=0.0, soft_max=1.0, precision=3, update=_update_dynamic_hair_band_item)
    curvature: FloatProperty(name='Crown Curve', default=0.18, min=-1.0, max=1.0, precision=3, update=_update_dynamic_hair_band_item)
    spot_density: FloatProperty(name='Spot Density', default=9.0, min=0.25, max=40.0, precision=2, update=_update_dynamic_hair_band_item)
    spot_gap: FloatProperty(name='Spot Gap', default=0.22, min=0.0, max=0.48, precision=3, update=_update_dynamic_hair_band_item)
    spot_shape: EnumProperty(name='Shape', items=[
        ('RIBBON', 'Band / Dashes', 'Curved segmented band'),
        ('ROUND', 'Circular Spots', 'Circular highlight spots'),
        ('OVAL', 'Oval Spots', 'Stretched circular spots'),
    ], default='ROUND', update=_update_dynamic_hair_band_item)
    spot_aspect: FloatProperty(name='Oval Aspect', default=1.0, min=0.20, max=4.0, precision=2, update=_update_dynamic_hair_band_item)
    strand_detail: FloatProperty(name='Strand Detail', default=0.35, min=0.0, max=1.0, precision=3, update=_update_dynamic_hair_band_item)


class GENOS_HairToonBandItem(bpy.types.PropertyGroup):
    """One independently editable hair toon-shadow region."""
    label: StringProperty(name='Name', default='Toon Band')
    threshold: FloatProperty(name='Threshold', default=0.5, min=0.0, max=1.0, precision=3, update=_update_dynamic_hair_toon_item)
    feather: FloatProperty(name='Feather', default=0.02, min=0.0001, max=0.5, precision=4, update=_update_dynamic_hair_toon_item)
    strength: FloatProperty(name='Tint Strength', default=1.0, min=0.0, max=1.0, precision=3, update=_update_dynamic_hair_toon_item)
    color: FloatVectorProperty(name='Shadow Tint', subtype='COLOR', size=4, min=0.0, max=1.0, default=(0.75, 0.55, 0.62, 1.0), update=_update_dynamic_hair_toon_item)

def register_scene_props():
    bpy.types.Scene.genos_hair_halo_count = IntProperty(name='Legacy Halo Count', default=2, min=1, max=64, update=_update_hair_halo_live)
    bpy.types.Scene.genos_hair_bands = bpy.props.CollectionProperty(type=GENOS_HairBandItem)
    bpy.types.Scene.genos_hair_band_index = IntProperty(name='Active Hair Band', default=0, min=0)
    bpy.types.Scene.genos_hair_toon_bands = bpy.props.CollectionProperty(type=GENOS_HairToonBandItem)
    bpy.types.Scene.genos_hair_toon_band_index = IntProperty(name='Active Hair Toon Band', default=0, min=0)
    bpy.types.Scene.genos_hair_halo_softness = FloatProperty(name='Halo Edge Softness', default=0.22, min=0.01, max=1.0, update=_update_hair_halo_live)
    bpy.types.Scene.genos_hair_halo_curve_center = FloatProperty(
        name='Halo Curve Center', default=0.50, min=-2.0, max=2.0, soft_min=0.0, soft_max=1.0,
        precision=3, update=_update_hair_halo_live,
        description='Horizontal center of the crown curve; 0.5 centers the bend on the hair mesh')
    bpy.types.Scene.genos_hair_halo_curvature = FloatProperty(
        name='Halo Curvature', default=0.18, min=-1.0, max=1.0, precision=3,
        update=_update_hair_halo_live,
        description='Bends highlight bands around the crown like painted anime hair highlights')
    bpy.types.Scene.genos_hair_halo_blur = FloatProperty(
        name='Halo Blur', default=0.18, min=0.0, max=1.0, precision=3,
        update=_update_hair_halo_live,
        description='Live feather/blur for band and round-spot edges')
    bpy.types.Scene.genos_hair_halo_spot_density = FloatProperty(
        name='Spot Density', default=9.0, min=0.25, max=40.0, precision=2,
        update=_update_hair_halo_live,
        description='Number/frequency of highlight marks across the curved crown band')
    bpy.types.Scene.genos_hair_halo_spot_gap = FloatProperty(
        name='Spot Gap', default=0.22, min=0.0, max=0.48, precision=3,
        update=_update_hair_halo_live,
        description='Distance between bright highlight marks; larger values separate the spots more')
    bpy.types.Scene.genos_hair_halo_spot_shape = EnumProperty(
        name='Halo Spot Shape',
        items=[
            ('RIBBON', 'Band / Dashes', 'Curved segmented highlight band'),
            ('ROUND', 'Circular Spots', 'Rounded reference-style highlight spots'),
            ('OVAL', 'Oval Spots', 'Rounded spots stretched by Spot Aspect'),
        ],
        default='ROUND', update=_update_hair_halo_live)
    bpy.types.Scene.genos_hair_halo_spot_aspect = FloatProperty(
        name='Spot Aspect', default=1.35, min=0.20, max=4.0, precision=2,
        update=_update_hair_halo_live,
        description='Horizontal stretch used by Oval Spots; 1.0 is circular')
    bpy.types.Scene.genos_hair_halo1_color = bpy.props.FloatVectorProperty(
        name='Primary Halo Color', subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 0.95, 0.975, 1.0), update=_update_hair_halo_live,
        description='Near-white pink highlight color, editable live')
    bpy.types.Scene.genos_hair_halo2_color = bpy.props.FloatVectorProperty(
        name='Halo 2 Color', subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 0.70, 0.80, 1.0), update=_update_hair_halo_live,
        description='Secondary pink highlight band color, editable live')
    bpy.types.Scene.genos_hair_halo3_color = bpy.props.FloatVectorProperty(
        name='Halo 3 Color', subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.95, 0.30, 0.48, 1.0), update=_update_hair_halo_live,
        description='Third / deeper colored halo band, editable live')
    for i, position, width, strength in [(2, 0.46, 0.032, 0.55), (3, 0.34, 0.024, 0.30)]:
        setattr(bpy.types.Scene, 'genos_hair_halo%d_pos' % i, FloatProperty(name='Position', default=position, min=-100, max=100, soft_min=0, soft_max=1, precision=3, update=_update_hair_halo_live))
        setattr(bpy.types.Scene, 'genos_hair_halo%d_width' % i, FloatProperty(name='Width', default=width, min=0.001, max=1, precision=3, update=_update_hair_halo_live))
        setattr(bpy.types.Scene, 'genos_hair_halo%d_strength' % i, FloatProperty(name='Strength', default=strength, min=0, max=1, update=_update_hair_halo_live))
    bpy.types.Scene.genos_emission_map_strength = FloatProperty(name="Emission Map Strength", default=1.0, min=0.0, max=20.0, description="True glow intensity; regenerate after changing")
    bpy.types.Scene.genos_hair_source_preservation = FloatProperty(name="Preserve Source Texture", default=0.15, min=0.0, max=1.0, update=_update_hair_halo_live, description="Live painted texture blend; high values reduce three-tone contrast")
    bpy.types.Scene.genos_output_dir = StringProperty(name="Output Directory", subtype='DIR_PATH', default="//")
    bpy.types.Scene.genos_texture_size = IntProperty(name="Texture Size", default=DEFAULT_SIZE, min=256, max=8192)
    
    # FIXED: Added the Paint Toggle and the New Mesh Copy Toggle
    bpy.types.Scene.genos_autotoggle_paint = BoolProperty(name="Auto Switch to Texture Paint", default=False)
    bpy.types.Scene.genos_export_mesh_copy = BoolProperty(name="Create Baked Mesh Copy", default=False, description="Generates a copy of the mesh with the exported textures applied")
    bpy.types.Scene.genos_autobake_fx_on_export = BoolProperty(name="Auto-Bake Material FX on Export", default=True, description="Synchronize procedural hair/eye material FX into ILM/Detail source maps before packing/export")
    bpy.types.Scene.genos_hair_transparency = FloatProperty(name="Hair Transparency", default=0.5, min=0.0, max=1.0, description="Strength of hair transparency when occluding skin/eyes")
    bpy.types.Scene.genos_hair_highlight_strength = FloatProperty(name="Hair Highlight Strength", default=1.0, min=0.0, max=2.0, update=_update_hair_halo_live, description="Live hair highlight intensity; also used when baking masks")
    bpy.types.Scene.genos_eye_sparkle_strength = FloatProperty(name="Eye FX Strength", default=1.0, min=0.0, max=2.0, description="Intensity of generated anime eye sparkle and iris masks")
    bpy.types.Scene.genos_2d_mouth = BoolProperty(name="Use 2D Anime Mouth", default=False, description="Enable flat color blending for 2D animated mouths")
    bpy.types.Scene.genos_normal_strength = FloatProperty(name="Normal Strength", default=1.0, min=0.0, max=4.0, precision=3, update=_update_surface_map_live, description="Tangent-space normal map strength")
    bpy.types.Scene.genos_normal_convention = EnumProperty(
        name="Normal Convention",
        items=[('OPENGL', 'OpenGL (+Y)', 'Standard Blender/OpenGL tangent normal'), ('DIRECTX', 'DirectX (-Y)', 'Invert the green/Y channel live without altering the source image')],
        default='OPENGL', update=_rebuild_surface_map_mode)
    bpy.types.Scene.genos_displacement_strength = FloatProperty(name="Displacement Strength", default=0.1, min=0.0, max=5.0, update=_update_surface_map_live, description="Bump distance and true displacement scale")
    bpy.types.Scene.genos_displacement_midlevel = FloatProperty(name="Displacement Midlevel", default=0.5, min=0.0, max=1.0, precision=3, update=_update_surface_map_live, description="Neutral height value; 0.5 is standard signed displacement")
    bpy.types.Scene.genos_true_displacement = BoolProperty(name="Cycles True Displacement", default=False, update=_rebuild_surface_map_mode, description="Also connect height to Material Output displacement; Eevee continues using bump")
    bpy.types.Scene.genos_bake_displacement = BoolProperty(name="Bake Displacement", default=True, description="Bake the current height/displacement signal into a data texture")
    bpy.types.Scene.genos_lineart_preset = EnumProperty(
        name="Lineart Preset",
        items=[
            ("CUSTOM", "Custom", "Use manual lineart values"),
            ("ULTRA_FINE", "Ultra Fine", "Tiny radius and tight thresholds for micro lines"),
            ("BALANCED", "Balanced", "General purpose anime lineart settings"),
            ("CRISP_INK", "Crisp Ink", "Hard, ink-like sharp lines"),
            ("SOFT_ANIME", "Soft Anime", "Softer and smoother line transitions"),
        ],
        default="BALANCED",
        update=_lineart_preset_update
    )

    bpy.types.Scene.genos_clothing_pattern_type = EnumProperty(
        name="Clothing Pattern",
        items=[
            ("NONE", "None", "Disable procedural clothing overlay"),
            ("PANTYHOSE", "Pantyhose", "Fine mesh pattern"),
            ("STRIPES", "Striped Cloth", "Banded stripe pattern"),
            ("RIPPED", "Ripped Cloth", "Torn cloth style breakup"),
            ("BODYSUIT_HEX", "Bodysuit Hex", "Sci-fi hex bodysuit pattern"),
            ("DOTS", "Dots", "Polka or micro-dot pattern"),
            ("COTTON", "Cotton", "Soft woven cotton microfiber pattern"),
            ("LEATHER", "Leather", "Leather grain and pore texture pattern"),
        ],
        default="NONE"
    )
    bpy.types.Scene.genos_pattern_scale = FloatProperty(name="Pattern Scale", default=20.0, min=0.01, max=200.0)
    bpy.types.Scene.genos_pattern_strength = FloatProperty(name="Pattern Strength", default=0.55, min=0.0, max=1.0)
    bpy.types.Scene.genos_pattern_rotation = FloatProperty(name="Pattern Rotation", default=0.0, min=-6.283185, max=6.283185, subtype='ANGLE')
    bpy.types.Scene.genos_pattern_cache_dir = StringProperty(name="Pattern Cache Directory", subtype='DIR_PATH', default="")
    bpy.types.Scene.genos_pattern_url_pantyhose = StringProperty(name="Pantyhose URL", default="https://ambientcg.com/get?file=Fabric001_1K-JPG.zip")
    bpy.types.Scene.genos_pattern_url_stripes = StringProperty(name="Stripes URL", default="https://ambientcg.com/get?file=Fabric002_1K-JPG.zip")
    bpy.types.Scene.genos_pattern_url_ripped = StringProperty(name="Ripped URL", default="https://ambientcg.com/get?file=Fabric003_1K-JPG.zip")
    bpy.types.Scene.genos_pattern_url_bodysuit = StringProperty(name="Bodysuit URL", default="https://ambientcg.com/get?file=Fabric004_1K-JPG.zip")
    bpy.types.Scene.genos_pattern_url_dots = StringProperty(name="Dots URL", default="https://ambientcg.com/get?file=Fabric005_1K-JPG.zip")
    bpy.types.Scene.genos_pattern_url_cotton = StringProperty(name="Cotton URL", default="https://ambientcg.com/get?file=Fabric006_1K-JPG.zip")
    bpy.types.Scene.genos_pattern_url_leather = StringProperty(name="Leather URL", default="https://ambientcg.com/get?file=Leather001_1K-JPG.zip")
    bpy.types.Scene.genos_pattern_last_download_report = StringProperty(name="Last Download Report", default="")
    bpy.types.Scene.genos_pattern_tint = bpy.props.FloatVectorProperty(
        name="Pattern Tint",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
        description="Tint color applied by the clothing pattern layer"
    )
    
    bpy.types.Scene.genos_create_shader_type = EnumProperty(
        name="Shader Type",
        items=[
            ("DEFAULT", "Default Shader", "Standard Anime Shader"),
            ("FACE", "Face Shader (SDF)", "Uses an SDF map for precise shadow thresholds"),
            ("HAIR", "Hair Shader", "Uses Anisotropic highlighting"),
        ],
        default="DEFAULT"
    )

    # --- 3-Banded Cel Shading (Nikke / Wuwa / PGR Style) ---
    bpy.types.Scene.genos_shadow_band1_thresh = FloatProperty(
        name="Band 1 Threshold (Lit/Mid)",
        default=0.52, min=0.0, max=1.0, precision=3,
        description="Threshold between lit region and 1st midtone shadow"
    )
    bpy.types.Scene.genos_shadow_band2_thresh = FloatProperty(
        name="Band 2 Threshold (Mid/Core)",
        default=0.28, min=0.0, max=1.0, precision=3,
        description="Threshold between 1st midtone shadow and 2nd deep core shadow"
    )
    bpy.types.Scene.genos_shadow_feather = FloatProperty(
        name="Shadow Edge Feather",
        default=0.035, min=0.001, max=0.5, precision=4,
        description="Softness/feathering of cel shadow boundaries"
    )
    bpy.types.Scene.genos_shadow_color_1 = bpy.props.FloatVectorProperty(
        name="1st Shadow Tint (Midtone)",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.72, 0.68, 0.75, 1.0),
        description="1st shadow tint multiplier (warmer midtone shadow)"
    )
    bpy.types.Scene.genos_shadow_color_2 = bpy.props.FloatVectorProperty(
        name="2nd Shadow Tint (Core)",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.42, 0.38, 0.52, 1.0),
        description="2nd shadow tint multiplier (deeper core shadow)"
    )
    bpy.types.Scene.genos_terminator_fringe_enable = BoolProperty(
        name="Enable SSS Terminator Fringe",
        default=True,
        description="Enable warm illustrative subsurface fringe at shadow boundary"
    )
    bpy.types.Scene.genos_terminator_fringe_color = bpy.props.FloatVectorProperty(
        name="Fringe Color",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 0.35, 0.25, 1.0),
        description="Vibrant warm fringe color at the shadow transition edge"
    )
    bpy.types.Scene.genos_terminator_fringe_intensity = FloatProperty(
        name="Fringe Strength",
        default=0.55, min=0.0, max=2.0,
        description="Intensity of the warm terminator edge glow"
    )

    # --- Anime Hair Studio Shading Properties ---
    bpy.types.Scene.genos_hair_band1_thresh = FloatProperty(
        update=_update_hair_halo_live,
        name="Hair Band 1 Threshold (Lit/Mid)",
        default=0.28, min=0.0, max=1.0, precision=3,
        description="Threshold between lit region and midtone cel shadow on hair (keeps hair radiant and lit)"
    )
    bpy.types.Scene.genos_hair_band2_thresh = FloatProperty(
        update=_update_hair_halo_live,
        name="Hair Band 2 Threshold (Mid/Core)",
        default=0.14, min=0.0, max=1.0, precision=3,
        description="Threshold between midtone and deep core cel shadow on hair"
    )
    bpy.types.Scene.genos_hair_feather = FloatProperty(
        update=_update_hair_halo_live,
        name="Hair Shadow Feather",
        default=0.045, min=0.001, max=0.5, precision=4,
        description="Softness/feathering of cel shadow boundaries on hair"
    )
    bpy.types.Scene.genos_hair_shadow_color_1 = bpy.props.FloatVectorProperty(
        update=_update_hair_halo_live,
        name="Hair 1st Shadow Tint (Ruby)",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.85, 0.48, 0.58, 1.0),
        description="1st shadow tint multiplier for hair (warm ruby crimson multiplier, avoiding muddy desaturated tones)"
    )
    bpy.types.Scene.genos_hair_shadow_color_2 = bpy.props.FloatVectorProperty(
        update=_update_hair_halo_live,
        name="Hair 2nd Deep Shadow Tint",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.58, 0.22, 0.32, 1.0),
        description="2nd deep shadow tint multiplier for hair (deep velvet crimson multiplier)"
    )
    bpy.types.Scene.genos_hair_highlight_tint = bpy.props.FloatVectorProperty(
        update=_update_hair_halo_live,
        name="Hair Highlight Tint",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 0.82, 0.88, 1.0),
        description="Core glint tint; the three halo band colors are controlled separately above"
    )
    bpy.types.Scene.genos_hair_pbr_spec_str = FloatProperty(
        update=_update_hair_halo_live,
        name="PBR Hair Glossiness",
        default=0.0, min=0.0, max=1.0, precision=3,
        description="PBR glossy specular strength on hair cards (set to 0 for satin/matte 2D anime finish)"
    )
    bpy.types.Scene.genos_hair_halo_coord_mode = EnumProperty(
        update=_update_hair_halo_live,
        name="Halo Coordinate Mode",
        items=[
            ("GENERATED_Z", "Mesh Height 0-1 (Universal)", "Normalized mesh bounding height (0.0=bottom, 1.0=top) - auto-adapts to all hair styles and scales"),
            ("OBJECT_Z", "Object Height (Meters)", "Elevation in 3D object space (meters from origin)"),
            ("UV_V", "UV Strand V", "Card UV root-to-tip V coordinate"),
            ("NORMAL_Z", "Surface Normal.Z", "Geometry surface normal Z elevation"),
        ],
        default="GENERATED_Z",
        description="Coordinate system used to position the anime hair angel ring"
    )
    bpy.types.Scene.genos_hair_halo_pos = FloatProperty(
        update=_update_hair_halo_live,
        name="Halo Height Position",
        default=0.58, min=-100.0, max=100.0, soft_min=0.0, soft_max=1.0, precision=3,
        description="Vertical shift position for hair anisotropic specular ring"
    )
    bpy.types.Scene.genos_hair_halo_width = FloatProperty(
        update=_update_hair_halo_live,
        name="Halo Width",
        default=0.15, min=0.01, max=1.0,
        description="Width of the hair angel ring specular highlight"
    )
    bpy.types.Scene.genos_hair_strands_strength = FloatProperty(
        update=_update_hair_halo_live,
        name="Hair Strand Glints",
        default=0.6, min=0.0, max=2.0,
        description="Intensity of fine vertical hair strand specular highlights"
    )
    bpy.types.Scene.genos_hair_strands_scale = FloatProperty(
        update=_update_hair_halo_live,
        name="Strands Glint Density",
        default=24.0, min=2.0, max=120.0, precision=1,
        description="Frequency of vertical hair strand specular cuts (higher for silky hair, lower for chunky anime locks)"
    )
    bpy.types.Scene.genos_hair_color_preset = EnumProperty(
        name="Hair Color Preset",
        items=[
            ("NIKKE_RED", "Nikke Poppy Red", "Radiant poppy coral red with ruby crimson shadows"),
            ("BLONDE", "Golden Blonde", "Sunny anime blonde with warm honey amber shadows"),
            ("SILVER_WHITE", "Silver / Platinum", "Platinum/silver hair with soft slate lavender shadows"),
            ("RAVEN_BLACK", "Raven Black", "Anime midnight black with deep indigo shadows"),
            ("SAKURA_PINK", "Sakura Pink", "Anime pastel pink with vibrant magenta shadows"),
            ("AZURE_BLUE", "Azure Blue", "Azure cyan with deep sapphire shadows"),
            ("EMERALD_GREEN", "Emerald Green", "Anime mint/emerald with deep teal shadows"),
            ("BRUNETTE_BROWN", "Warm Brunette", "Warm chestnut brown with rich mahogany shadows"),
        ],
        default="NIKKE_RED",
        description="Quick calibrated color presets matching popular 2D anime styles"
    )
    def update_hair_ombre_props(self, context):
        scene = context.scene if context else bpy.context.scene
        tint = tuple(getattr(scene, "genos_hair_tip_tint", (0.92, 0.32, 0.52, 1.0)))
        strength = float(getattr(scene, "genos_hair_tip_strength", 0.85))
        spread = float(getattr(scene, "genos_hair_ombre_range", 0.35))
        power = float(getattr(scene, "genos_hair_ombre_power", 1.5))
        blend = getattr(scene, "genos_hair_ombre_blend", "MIX")

        for mat in bpy.data.materials:
            if not getattr(mat, "use_nodes", False) or not mat.node_tree:
                continue
            mix_node = mat.node_tree.nodes.get("Hair Tip Color Mix")
            if mix_node:
                try:
                    mix_node.blend_type = blend
                except Exception:
                    pass
                sock_b = find_socket(mix_node.inputs, "B", "Color2")
                if sock_b:
                    sock_b.default_value = tint

            range_node = mat.node_tree.nodes.get("Hair Ombre Map Range")
            if range_node:
                from_min = max(0.0, 1.0 - spread)
                if "From Min" in range_node.inputs:
                    range_node.inputs["From Min"].default_value = from_min

            power_node = mat.node_tree.nodes.get("Hair Ombre Power")
            if power_node and len(power_node.inputs) > 1:
                power_node.inputs[1].default_value = power

            gain_node = mat.node_tree.nodes.get("Hair Ombre Master Gain")
            if gain_node and len(gain_node.inputs) > 1:
                gain_node.inputs[1].default_value = strength

    bpy.types.Scene.genos_hair_tip_tint = bpy.props.FloatVectorProperty(
        name="Hair Ombre Tint",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.92, 0.32, 0.52, 1.0),
        description="Ombre color tint applied toward hair tips (sakura pink, crimson, violet, cyan, etc.)",
        update=update_hair_ombre_props
    )
    bpy.types.Scene.genos_hair_tip_strength = FloatProperty(
        name="Ombre Intensity",
        default=0.85, min=0.0, max=1.0,
        description="Master opacity / strength of the hair tip ombre effect",
        update=update_hair_ombre_props
    )
    bpy.types.Scene.genos_hair_ombre_range = FloatProperty(
        name="Ombre Spread",
        default=0.35, min=0.01, max=1.0, precision=2,
        description="Length of ombre gradient extending upward from hair tips",
        update=update_hair_ombre_props
    )
    bpy.types.Scene.genos_hair_ombre_power = FloatProperty(
        name="Ombre Falloff Power",
        default=1.5, min=0.1, max=5.0, precision=2,
        description="Falloff sharpness curve (higher = concentrated at edge tips, lower = soft gradient)",
        update=update_hair_ombre_props
    )
    bpy.types.Scene.genos_hair_ombre_blend = EnumProperty(
        name="Ombre Blend Mode",
        items=[
            ("MIX", "Mix (Tint)", "Smooth direct color blend between base hair and ombre tint"),
            ("MULTIPLY", "Multiply (Deep Dye)", "Darkens and deeply saturates tips with rich color dye"),
            ("OVERLAY", "Overlay (Vibrant)", "High-contrast punchy anime video game gradient"),
            ("ADD", "Add (Glow / Fantasy)", "Luminous fantasy glowing hair tips"),
        ],
        default="MIX",
        description="Blending mode for applying ombre color to hair base color",
        update=update_hair_ombre_props
    )
    def _update_hair_ombre_coord(self, context):
        _rebuild_dynamic_hair_materials(context)

    bpy.types.Scene.genos_hair_ombre_coord_mode = EnumProperty(
        name="Ombre Coordinate Mode",
        items=[
            ("MESH", "Mesh Geometry (Auto-Clumps)", "Reads 'Hair_Ombre' 3D vertex attribute generated by Detect Ombre"),
            ("UV", "UV Coordinate (1.0 - Y)", "Uses vertical UV coordinates (for standard top-to-bottom hair cards)"),
            ("OBJECT_Z", "Object Height (Z)", "Uses vertical Z height in object space"),
        ],
        default="MESH",
        description="Source coordinates for calculating root-to-tip ombre gradient",
        update=_update_hair_ombre_coord
    )

    # --- Authentic 2D Anime Clothing & Specular Studio ---
    bpy.types.Scene.genos_cloth_spec_mode = EnumProperty(
        name="Cloth Specular Mode",
        items=[
            ("HYBRID_AUTO", "Hybrid Auto (ILM.B Driven)", "Decodes matte fabric, satin/leather, or metal using ILM.B texture map"),
            ("ANIME_MATTE", "2D Matte Fabric", "Soft velvet microfiber grazing sheen, zero plastic gloss (for cotton, wool, linen)"),
            ("CRISP_CEL", "Crisp Cel Glints", "Sharp anime cel highlight bands (for leather, latex, vinyl, bodysuit)"),
            ("METALLIC", "Metallic Armor & Trims", "Gold, silver, and metal armor reflections tinted by base color"),
        ],
        default="HYBRID_AUTO",
        description="Shading model for clothing fabrics, leather, and armor"
    )
    bpy.types.Scene.genos_cloth_velvet_sheen = FloatProperty(
        name="Cloth Velvet Sheen",
        default=0.40, min=0.0, max=2.0, precision=2,
        description="Strength of soft grazing-angle microfiber velvet sheen on clothing"
    )
    bpy.types.Scene.genos_cloth_velvet_power = FloatProperty(
        name="Cloth Velvet Power",
        default=2.8, min=0.5, max=8.0, precision=1,
        description="Tightness of the velvet falloff towards clothing edges"
    )
    bpy.types.Scene.genos_cloth_shadow_bounce = bpy.props.FloatVectorProperty(
        name="Cloth Shadow Ambient Bounce",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.70, 0.72, 0.85, 1.0),
        description="Soft ambient environmental fill in clothing shadows (prevents dead gray shadows)"
    )
    bpy.types.Scene.genos_cloth_spec_str = FloatProperty(
        name="Cloth Cel Glint Strength",
        default=0.45, min=0.0, max=2.0, precision=2,
        description="Intensity of sharp stylized anime highlight bands on leather/bodysuit"
    )
    bpy.types.Scene.genos_spec_core_strength = FloatProperty(
        name="Specular Core Intensity",
        default=1.0, min=0.0, max=5.0,
        description="Sharp primary anime highlight glint strength"
    )
    bpy.types.Scene.genos_spec_halo_strength = FloatProperty(
        name="Specular Halo Sheen",
        default=0.4, min=0.0, max=2.0,
        description="Secondary soft specular sheen strength"
    )
    bpy.types.Scene.genos_spec_tint = bpy.props.FloatVectorProperty(
        name="Specular Tint",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
        description="Tint color for specular highlights"
    )
    bpy.types.Scene.genos_spec_metallic = BoolProperty(
        name="Metallic Specular Mode",
        default=False,
        description="Tint specular by base albedo and allow reflections in shadow (for mecha/armor)"
    )

    def update_outline_props(self, context):
        scene = context.scene if context else bpy.context.scene
        thickness = float(getattr(scene, "genos_outline_thickness", 0.004))
        color = tuple(getattr(scene, "genos_outline_color", (0.08, 0.04, 0.06, 1.0)))
        mat = bpy.data.materials.get("AnimeToon_Outline")
        if mat and mat.use_nodes:
            emit = mat.node_tree.nodes.get("Outline Emission")
            if emit and "Color" in emit.inputs:
                emit.inputs["Color"].default_value = color
        for o in bpy.data.objects:
            if o.type == 'MESH':
                mod = o.modifiers.get("Anime Outline")
                if mod:
                    mod.thickness = thickness

    bpy.types.Scene.genos_outline_thickness = FloatProperty(
        name="Outline Thickness",
        default=0.004, min=0.0005, max=0.05, precision=4,
        description="Thickness of the anime inverted hull outline lineart",
        update=update_outline_props
    )
    bpy.types.Scene.genos_outline_color = bpy.props.FloatVectorProperty(
        name="Outline Color",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.08, 0.04, 0.06, 1.0),
        description="Ink color for anime outline lineart (deep warm tint prevents harsh black)",
        update=update_outline_props
    )

    # --- Emission Channel, Overdrive & Special Effects ---
    bpy.types.Scene.genos_emission_channel = EnumProperty(
        name="Emission Channel",
        items=[
            ("RGBA", "RGBA (Full Color)", "Use full color emission map"),
            ("R", "R Channel", "Use Red channel"),
            ("G", "G Channel", "Use Green channel"),
            ("B", "B Channel", "Use Blue channel"),
            ("A", "Alpha Channel", "Use Alpha channel"),
        ],
        default="RGBA"
    )
    bpy.types.Scene.genos_emission_tint = bpy.props.FloatVectorProperty(
        name="Emission Accent Tint",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 0.45, 0.1, 1.0),
        description="Accent color tint for ILM and detail glow masks (sci-fi tech glow)"
    )
    bpy.types.Scene.genos_emission_pulse_enable = BoolProperty(
        name="Sci-Fi Breathing Pulse",
        default=False,
        description="Animate subtle pulsation on tech lines and emissive details"
    )
    bpy.types.Scene.genos_emission_pulse_speed = FloatProperty(
        name="Pulse Speed",
        default=2.0, min=0.1, max=10.0,
        description="Speed of the emissive breathing pulse"
    )
    bpy.types.Scene.genos_rim_color = bpy.props.FloatVectorProperty(
        name="Rim Light Color",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(0.90, 0.94, 1.0, 1.0),
        description="Stylized rim light color (e.g. cool cyan or warm sunset)"
    )
    bpy.types.Scene.genos_rim_power = FloatProperty(
        name="Rim Falloff Power",
        default=2.5, min=0.5, max=8.0,
        description="Tightness and sharpness of the rim light edge"
    )
    bpy.types.Scene.genos_rim_intensity = FloatProperty(
        name="Rim Light Strength",
        default=1.0, min=0.0, max=5.0,
        description="Intensity multiplier for the rim lighting"
    )
    bpy.types.Scene.genos_rim_directional = BoolProperty(
        name="Directional Backlight Mode",
        default=True,
        description="Concentrate rim light opposite the main key light for dramatic anime backlighting"
    )

    bpy.types.Scene.genos_exp_suf_albedo = StringProperty(name="BaseColor Name", default="_BaseColor")
    bpy.types.Scene.genos_exp_suf_emission = StringProperty(name="Emission Name", default="_Emission")
    bpy.types.Scene.genos_exp_suf_normal = StringProperty(name="Normal Name", default="_Normal")
    bpy.types.Scene.genos_exp_suf_roughness = StringProperty(name="Roughness Name", default="_Roughness")
    bpy.types.Scene.genos_exp_suf_metallic = StringProperty(name="Metallic Name", default="_Metallic")
    bpy.types.Scene.genos_exp_suf_opacity = StringProperty(name="Opacity Name", default="_Opacity")
    bpy.types.Scene.genos_exp_suf_ao = StringProperty(name="AO Name", default="_AO")
    bpy.types.Scene.genos_exp_suf_ilm = StringProperty(name="ILM Name", default="_ILM")
    bpy.types.Scene.genos_exp_suf_detail = StringProperty(name="Detail Name", default="_Detail")
    bpy.types.Scene.genos_exp_suf_sdf = StringProperty(name="SDF Name", default="_SDF")
    bpy.types.Scene.genos_exp_suf_displacement = StringProperty(name="Displacement Name", default="_Displacement")

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

    # --- Selection & Multi-Object Isolation Properties ---
    bpy.types.Scene.genos_selection_effect_target = EnumProperty(
        name="Effect Target",
        items=[
            ("ILM_EMISSION", "Tech Glow / Emission (ILM.G)", "Paint glow mask on selected faces"),
            ("ILM_SPEC", "Sharp Specular / Glint (ILM.B)", "Paint specular highlight on selected faces"),
            ("PATTERN_MASK", "Clothing Pattern Mask", "Paint clothing pattern mask on selected faces"),
            ("ILM_RIM", "Rim Highlight Mask (ILM.A)", "Paint rim reflection mask on selected faces"),
            ("ILM_SHADOW", "Shadow Bias (ILM.R)", "Paint shadow threshold bias on selected faces"),
            ("DETAIL_ACCENT", "Accent / Ombre (Detail.B)", "Paint accent / depth on selected faces"),
            ("BASECOLOR", "BaseColor Fill", "Fill albedo color on selected faces"),
        ],
        default="ILM_EMISSION"
    )
    bpy.types.Scene.genos_selection_fill_value = FloatProperty(
        name="Fill Value",
        default=1.0, min=0.0, max=1.0,
        description="Grayscale value to fill into the selected faces' mask"
    )
    bpy.types.Scene.genos_selection_fill_color = FloatVectorProperty(
        name="Fill Color",
        subtype='COLOR', size=4, min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
        description="Color to fill on selected faces (for BaseColor or color maps)"
    )
    bpy.types.Scene.genos_selection_fork_preset = EnumProperty(
        name="Selection Preset",
        items=[
            ("TECH_GLOW", "Sci-Fi Tech Glow", "Variant with pulsing sci-fi emission enabled and boosted glow"),
            ("MECHA_ARMOR", "Mecha / Metallic Armor", "Metallic anime specular mode with sharp core glint"),
            ("HAIR_ACCENT", "Hair Ombre / Halo", "Anisotropic angel ring & tip gradient on selected hair locks"),
            ("CLOTHING_PATTERN", "Clothing Pattern", "Assigns clothing pattern layer"),
            ("CUSTOM_VARIANT", "Custom Variant Clone", "Independent material slot for custom tweaking"),
        ],
        default="TECH_GLOW"
    )
    bpy.types.Scene.genos_selection_vertex_fx_channel = EnumProperty(
        name="Vertex FX Channel",
        items=[
            ("GLOW", "Tech Glow (Red)", "Add emission boost to selected vertices"),
            ("SPEC_MECHA", "Mecha Specular (Green)", "Add metallic specular boost to selected vertices"),
            ("RIM", "Backlight Rim (Blue)", "Add rim light boost to selected vertices"),
            ("CLEAR", "Clear FX (Black)", "Remove FX from selected vertices"),
        ],
        default="GLOW"
    )
    bpy.types.Scene.genos_use_vertex_fx_mask = BoolProperty(
        name="Enable Mesh Vertex FX Attribute",
        default=True,
        description="Allow mesh Color Attribute 'Anime_FX' to modulate glow, specular, and rim per-object or per-selection"
    )

    # --- RGB Shadow Map → ILM Converter Properties (Mihoyo / GI-style) ---
    bpy.types.Scene.genos_rgb_shadow_map = PointerProperty(
        name="RGB Shadow Map (Input)",
        type=bpy.types.Image,
        description="Older toon shader shadow map: R=outline mask, G=shadow threshold, B=specular intensity, A=rim mask"
    )
    bpy.types.Scene.genos_rgb_shadow_spec_threshold = FloatProperty(
        name="Specular Threshold",
        default=0.85,
        min=0.0,
        max=1.0,
        description="B-channel luminance value above which pixels are treated as specular highlights (ILM.B output)"
    )
    bpy.types.Scene.genos_rgb_shadow_invert = BoolProperty(
        name="Invert Shadow",
        default=False,
        description="Flip shadow polarity: enable if dark areas in your map represent lit regions"
    )
    bpy.types.Scene.genos_rgb_shadow_mode = EnumProperty(
        name="Shadow Map Format",
        items=[
            ("COLORZONE", "Color Zone / Flat-Color (Default)",
             "Your shadow map uses flat colored zones per material area (pink skin, dark shadow, etc). "
             "Luminance drives shadow, Sobel edges become rim."),
            ("PERCHANNEL", "Per-Channel (Mihoyo/GI)",
             "Your shadow map encodes data per channel: R=outline, G=shadow threshold, B=specular, A=rim."),
        ],
        default="COLORZONE",
        description="Select the format of the source shadow map so the converter extracts data correctly"
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
            ("PATTERN_MASK", "Pattern Mask (Clothes Layer)", ""),
        ],
        default="BASECOLOR"
    )
    
    bpy.types.Scene.genos_lineart_radius = FloatProperty(
        name="Edge Radius",
        default=0.03,
        min=1e-08,
        max=0.5,
        precision=8,
        description="Bevel radius used for curvature lineart. Supports ultra-small values for tiny details"
    )
    bpy.types.Scene.genos_lineart_edge_min = FloatProperty(
        name="Lineart Edge Min",
        default=0.01,
        min=0.0,
        max=1.0,
        precision=6,
        description="Lower threshold for generated lineart mask"
    )
    bpy.types.Scene.genos_lineart_edge_max = FloatProperty(
        name="Lineart Edge Max",
        default=0.15,
        min=0.0,
        max=1.0,
        precision=6,
        description="Upper threshold for generated lineart mask"
    )
    bpy.types.Scene.genos_lineart_gamma = FloatProperty(
        name="Lineart Sharpness",
        default=1.0,
        min=0.1,
        max=8.0,
        precision=3,
        description="Power curve for line sharpness; higher values create crisper lines"
    )
    bpy.types.Scene.genos_lineart_smooth = BoolProperty(
        name="Smooth Sharp Bake",
        default=True,
        description="Uses smoothstep remapping before sharpness to keep bakes smooth but crisp"
    )
    bpy.types.Scene.genos_lineart_samples = IntProperty(
        name="Lineart Samples",
        default=12,
        min=1,
        max=64,
        description="Bevel samples for curvature capture; higher values reduce noise"
    )

    bpy.types.Material.genos_base_color_map = image_prop("Base Color Map", update=_update_mat_basecolor)
    bpy.types.Material.genos_emission_map = image_prop("Emission Map", update=_update_mat_emission)
    bpy.types.Material.genos_normal_map = image_prop("Normal Map", update=_update_mat_normal)
    bpy.types.Material.genos_roughness_map = image_prop("Roughness Map", update=_update_mat_roughness)
    bpy.types.Material.genos_metallic_map = image_prop("Metallic Map", update=_update_mat_metallic)
    bpy.types.Material.genos_opacity_map = image_prop("Opacity / Alpha Map", update=_update_mat_opacity)
    bpy.types.Material.genos_ao_map = image_prop("AO Map", update=_update_mat_ao)
    bpy.types.Material.genos_ilm_packed = image_prop("ILM Packed")
    bpy.types.Material.genos_detail_packed = image_prop("Detail Packed")
    bpy.types.Material.genos_sdf_map = image_prop("SDF Map", update=_update_mat_sdf)
    bpy.types.Material.genos_displacement_map = image_prop("Displacement Map", update=_update_mat_displacement)
    bpy.types.Material.genos_pattern_color_map = image_prop("Pattern Color Map")
    bpy.types.Material.genos_pattern_roughness_map = image_prop("Pattern Roughness Map")
    bpy.types.Material.genos_pattern_normal_map = image_prop("Pattern Normal Map")

    # IMPORTANT: Do not touch bpy.data here. During legacy addon installation/enabling
    # Blender wraps bpy.data in _RestrictData, so accessing bpy.data.scenes from
    # register_scene_props() can abort addon installation. Dynamic band migration is
    # deferred until Blender leaves the restricted registration context.


def _initialize_dynamic_hair_bands_deferred():
    """Initialize/migrate hair bands only after bpy.data becomes available.

    Blender can expose bpy.data as _RestrictData while an addon is being enabled.
    Returning a short delay asks the timer to retry instead of failing installation.
    """
    try:
        scenes = getattr(bpy.data, 'scenes', None)
    except Exception:
        scenes = None

    if scenes is None:
        return 0.25

    try:
        for scene in scenes:
            try:
                _ensure_dynamic_hair_bands(scene)
                _ensure_dynamic_hair_toon_bands(scene)
            except Exception as exc:
                print("[Anime Studio] Could not initialize dynamic hair bands for scene %r: %s" % (getattr(scene, 'name', '<scene>'), exc))
    except Exception:
        # Data may still be transitioning during startup/file load. Retry shortly.
        return 0.25

    return None


@persistent
def _initialize_dynamic_hair_bands_on_load(_dummy=None):
    """Migrate legacy fixed halo fields whenever an older .blend is loaded."""
    try:
        scenes = getattr(bpy.data, 'scenes', None)
        if scenes is None:
            return
        for scene in scenes:
            try:
                _ensure_dynamic_hair_bands(scene)
                _ensure_dynamic_hair_toon_bands(scene)
            except Exception as exc:
                print("[Anime Studio] Hair-band migration on file load failed for %r: %s" % (getattr(scene, 'name', '<scene>'), exc))
    except Exception as exc:
        print("[Anime Studio] Hair-band migration on file load deferred/failed:", exc)


def _register_dynamic_hair_band_deferred_init():
    """Register safe post-enable and post-file-load initialization hooks."""
    try:
        if not bpy.app.timers.is_registered(_initialize_dynamic_hair_bands_deferred):
            bpy.app.timers.register(_initialize_dynamic_hair_bands_deferred, first_interval=0.10)
    except Exception as exc:
        print("[Anime Studio] Could not register deferred hair-band initializer:", exc)

    try:
        if _initialize_dynamic_hair_bands_on_load not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(_initialize_dynamic_hair_bands_on_load)
    except Exception as exc:
        print("[Anime Studio] Could not register hair-band load handler:", exc)


def _unregister_dynamic_hair_band_deferred_init():
    """Remove deferred initialization hooks cleanly on addon disable/reload."""
    try:
        if bpy.app.timers.is_registered(_initialize_dynamic_hair_bands_deferred):
            bpy.app.timers.unregister(_initialize_dynamic_hair_bands_deferred)
    except Exception:
        pass

    try:
        while _initialize_dynamic_hair_bands_on_load in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_initialize_dynamic_hair_bands_on_load)
    except Exception:
        pass


def unregister_scene_props():
    scene_props = ["genos_hair_bands", "genos_hair_band_index", "genos_hair_toon_bands", "genos_hair_toon_band_index", "genos_hair_halo_count", "genos_hair_halo_softness", "genos_hair_halo_curve_center", "genos_hair_halo_curvature", "genos_hair_halo_blur", "genos_hair_halo_spot_density", "genos_hair_halo_spot_gap", "genos_hair_halo_spot_shape", "genos_hair_halo_spot_aspect", "genos_hair_halo1_color", "genos_hair_halo2_color", "genos_hair_halo3_color", "genos_hair_halo2_pos", "genos_hair_halo2_width", "genos_hair_halo2_strength", "genos_hair_halo3_pos", "genos_hair_halo3_width", "genos_hair_halo3_strength", "genos_hair_source_preservation", "genos_emission_map_strength",
        "genos_output_dir", "genos_texture_size", "genos_base_name",
        "genos_autotoggle_paint", "genos_export_mesh_copy", "genos_autobake_fx_on_export", "genos_hair_transparency", "genos_hair_highlight_strength", "genos_eye_sparkle_strength", "genos_2d_mouth", "genos_normal_strength", "genos_normal_convention", "genos_displacement_strength", "genos_displacement_midlevel", "genos_true_displacement", "genos_bake_displacement",
        "genos_lineart_preset", "genos_clothing_pattern_type", "genos_pattern_scale", "genos_pattern_strength", "genos_pattern_rotation", "genos_pattern_tint", "genos_pattern_cache_dir",
        "genos_pattern_url_pantyhose", "genos_pattern_url_stripes", "genos_pattern_url_ripped", "genos_pattern_url_bodysuit", "genos_pattern_url_dots", "genos_pattern_url_cotton", "genos_pattern_url_leather",
        "genos_pattern_last_download_report",
        "genos_paint_target", "genos_exp_suf_albedo", "genos_exp_suf_emission", "genos_exp_suf_normal", "genos_exp_suf_roughness", "genos_exp_suf_metallic", "genos_exp_suf_opacity", "genos_exp_suf_ao", "genos_exp_suf_ilm",
        "genos_exp_suf_detail", "genos_exp_suf_sdf", "genos_exp_suf_displacement", "genos_spec_mat_type", "genos_lineart_radius", "genos_lineart_edge_min", "genos_lineart_edge_max", "genos_lineart_gamma", "genos_lineart_smooth", "genos_lineart_samples", "genos_create_shader_type",
        "genos_rgb_shadow_map", "genos_rgb_shadow_spec_threshold", "genos_rgb_shadow_invert", "genos_rgb_shadow_mode",
        "genos_shadow_band1_thresh", "genos_shadow_band2_thresh", "genos_shadow_feather",
        "genos_shadow_color_1", "genos_shadow_color_2",
        "genos_terminator_fringe_enable", "genos_terminator_fringe_color", "genos_terminator_fringe_intensity",
        "genos_hair_band1_thresh", "genos_hair_band2_thresh", "genos_hair_feather",
        "genos_hair_shadow_color_1", "genos_hair_shadow_color_2", "genos_hair_highlight_tint", "genos_hair_pbr_spec_str",
        "genos_hair_halo_coord_mode", "genos_hair_halo_pos", "genos_hair_halo_width", "genos_hair_strands_strength", "genos_hair_strands_scale", "genos_hair_color_preset",
        "genos_hair_tip_tint", "genos_hair_tip_strength", "genos_hair_ombre_range", "genos_hair_ombre_power", "genos_hair_ombre_blend", "genos_hair_ombre_coord_mode",
        "genos_cloth_spec_mode", "genos_cloth_velvet_sheen", "genos_cloth_velvet_power", "genos_cloth_shadow_bounce", "genos_cloth_spec_str",
        "genos_spec_core_strength", "genos_spec_halo_strength", "genos_spec_tint", "genos_spec_metallic",
        "genos_emission_channel", "genos_emission_tint", "genos_emission_pulse_enable", "genos_emission_pulse_speed",
        "genos_rim_color", "genos_rim_power", "genos_rim_intensity", "genos_rim_directional",
        "genos_selection_effect_target", "genos_selection_fill_value", "genos_selection_fill_color",
        "genos_selection_fork_preset", "genos_selection_vertex_fx_channel", "genos_use_vertex_fx_mask",
        "genos_outline_thickness", "genos_outline_color",
    ]
    for p in scene_props:
        if hasattr(bpy.types.Scene, p): delattr(bpy.types.Scene, p)

    mat_props = [
        "genos_base_color_map", "genos_emission_map", "genos_normal_map",
        "genos_roughness_map", "genos_metallic_map", "genos_opacity_map", "genos_ao_map",
        "genos_ilm_packed", "genos_detail_packed", "genos_sdf_map",
        "genos_displacement_map", "genos_pattern_color_map",
        "genos_pattern_roughness_map", "genos_pattern_normal_map"
    ]
    for p in mat_props:
        if hasattr(bpy.types.Material, p): delattr(bpy.types.Material, p)

# -------------------------------------------------------------------
# Operators
# -------------------------------------------------------------------

class GENOS_OT_convert_rgb_shadow_to_ilm(bpy.types.Operator):
    """Convert a Mihoyo/GI-style RGB shadow map into an ILM channel-packed map.
    Expects: R=outline mask, G=shadow threshold, B=specular intensity, A=rim mask.
    Output ILM: R=shadow, G=emission hint, B=specular, A=rim."""
    bl_idname = "genos.convert_rgb_shadow_to_ilm"
    bl_label = "Convert RGB Shadow → ILM"
    bl_description = (
        "Convert a Mihoyo/GI-style RGB shadow map (R=outline, G=shadow, B=spec, A=rim) "
        "into the ILM channel-packed format used by this shader. "
        "Writes result to the active material's ILM packed image."
    )

    def execute(self, context):
        scene = context.scene
        obj = context.active_object
        if not obj or not obj.active_material:
            self.report({'ERROR'}, "No active material found. Select a mesh with an AnimeToon material.")
            return {'CANCELLED'}

        mat = obj.active_material
        shadow_img = getattr(scene, 'genos_rgb_shadow_map', None)
        if shadow_img is None:
            self.report({'ERROR'}, "No RGB Shadow Map assigned. Pick one in the converter section.")
            return {'CANCELLED'}

        # Ensure ILM packed image exists on the material
        ilm_img = getattr(mat, 'genos_ilm_packed', None)
        if ilm_img is None:
            base = material_base_name(mat)
            size = scene_texture_size()
            ilm_img = make_image(
                f"{base}_ILM", size, size,
                alpha=True, colorspace=MASK_COLORSPACE,
                color=(0.5, 0.0, 0.0, 1.0)
            )
            configure_mask_image(ilm_img, packed=True)
            try:
                mat.genos_ilm_packed = ilm_img
            except Exception:
                pass

        spec_thresh = float(getattr(scene, 'genos_rgb_shadow_spec_threshold', 0.85))
        invert_shadow = bool(getattr(scene, 'genos_rgb_shadow_invert', False))
        shadow_mode = getattr(scene, 'genos_rgb_shadow_mode', 'COLORZONE')

        try:
            if shadow_mode == 'COLORZONE':
                convert_colorzone_shadow_to_ilm(
                    shadow_img, ilm_img,
                    spec_threshold=spec_thresh,
                    invert_shadow=invert_shadow
                )
            else:
                convert_rgb_shadow_to_ilm(
                    shadow_img, ilm_img,
                    spec_threshold=spec_thresh,
                    invert_shadow=invert_shadow
                )
        except Exception as e:
            self.report({'ERROR'}, f"Conversion failed: {e}")
            return {'CANCELLED'}

        # Update ILM source nodes in the active material node tree
        if mat.use_nodes:
            for node_name in ("ILM_Shadow", "ILM_Emission", "ILM_Spec", "ILM_Rim"):
                node = mat.node_tree.nodes.get(node_name)
                if node and hasattr(node, 'image'):
                    node.image = ilm_img
            # Also update the baked material ILM map node if present
            ilm_map_node = mat.node_tree.nodes.get("ILM MAP")
            if ilm_map_node and hasattr(ilm_map_node, 'image'):
                ilm_map_node.image = ilm_img

        self.report({'INFO'},
            f"Converted '{shadow_img.name}' → ILM map for '{mat.name}'. "
            f"Spec threshold={spec_thresh:.2f}, Invert={invert_shadow}. "
            "Click 'Clean/Regen Shader' to apply changes to the viewport.")
        return {'FINISHED'}


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

        base_node = mat.node_tree.nodes.get("BaseColor")
        if not base_node or not is_valid_image(getattr(base_node, 'image', None)) or image_is_nearly_black(base_node.image):
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
        reset_img("Pattern Mask", (0.0, 0.0, 0.0, 1.0))
        
        pack_material_detail(mat)
        pack_material_ilm(mat)
        self.report({'INFO'}, "Successfully restored all default texture data and repacked masks.")
        return {'FINISHED'}

class GENOS_OT_create_workspace(bpy.types.Operator):
    bl_idname = "genos.create_workspace"
    bl_label = "Create Shader Workspace"

    def execute(self, context):
        s = context.scene
        size = s.genos_texture_size

        obj = context.active_object
        source_mat = obj.active_material if obj else None
        extracted_source = extract_source_textures_from_material(source_mat) if source_mat else {}

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

        base_img = extracted_source.get("basecolor")
        if not is_valid_image(base_img):
            base_img = make_image(f"{mat_base}_BaseColor", size, size, alpha=True, colorspace="sRGB", color=(0.8, 0.8, 0.8, 1.0))
        
        emission_map = extracted_source.get("emission_map")
        if not is_valid_image(emission_map):
            emission_map = make_image(f"{mat_base}_EmissionMap", size, size, alpha=True, colorspace="sRGB", color=(0.0, 0.0, 0.0, 1.0))
            
        # Preserve any existing authored data maps from the source material.
        for key, prop, kind in (
            ('normal_map', 'genos_normal_map', 'NORMAL'),
            ('roughness_map', 'genos_roughness_map', 'ROUGHNESS'),
            ('metallic_map', 'genos_metallic_map', 'METALLIC'),
            ('opacity_map', 'genos_opacity_map', 'OPACITY'),
            ('ao_map', 'genos_ao_map', 'AO'),
            ('displacement_map', 'genos_displacement_map', 'DISPLACEMENT'),
        ):
            img = extracted_source.get(key)
            if is_valid_image(img):
                configure_standard_map_image(img, kind)
                try: setattr(mat, prop, img)
                except Exception: pass

        try:
            mat.genos_base_color_map = base_img
            mat.genos_emission_map = emission_map
        except Exception:
            pass
        
        # PACKED MAPS: create (RGBA) textures and ensure alpha is 1.0 to avoid export invisibility
        mat.genos_ilm_packed = make_image(f"{mat_base}_ILM", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.5, 0.0, 0.0, 1.0))
        mat.genos_detail_packed = make_image(f"{mat_base}_Detail", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(1.0, 0.0, 0.0, 1.0))
        configure_mask_image(mat.genos_ilm_packed, packed=True)
        configure_mask_image(mat.genos_detail_packed, packed=True)

        images = {
            "basecolor": base_img,
            "emission_map": emission_map,
            "normal_map": getattr(mat, "genos_normal_map", None),
            "roughness_map": getattr(mat, "genos_roughness_map", None),
            "metallic_map": getattr(mat, "genos_metallic_map", None),
            "opacity_map": getattr(mat, "genos_opacity_map", None),
            "ao_map": getattr(mat, "genos_ao_map", None),
            "ilm_shadow": make_image(f"{mat_base}_ILM_ShadowSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.5, 0.5, 0.5, 1.0)),
            "ilm_emission": make_image(f"{mat_base}_ILM_EmissionSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "ilm_spec": make_image(f"{mat_base}_ILM_SpecSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "ilm_rim": make_image(f"{mat_base}_ILM_RimSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "detail_ao": make_image(f"{mat_base}_Detail_AOSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(1.0, 1.0, 1.0, 1.0)),
            "detail_curve": make_image(f"{mat_base}_Detail_CurveSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "detail_accent": make_image(f"{mat_base}_Detail_AccentSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "detail_emission": make_image(f"{mat_base}_Detail_EmissionSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "pattern_mask": make_image(f"{mat_base}_PatternMask", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.0, 0.0, 0.0, 1.0)),
            "displacement_map": getattr(mat, "genos_displacement_map", None) or make_image(f"{mat_base}_Displacement", size, size, alpha=False, colorspace="Non-Color", color=(0.5, 0.5, 0.5, 1.0)),
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

def adapt_material_to_new_scheme(mat):
    """
    Adapts an existing material to the robust AnimeToon scheme:
    - Retains original textures (BaseColor, Normal, Emission)
    - Auto-detects material role ('FACE', 'HAIR', 'METALLIC', 'DEFAULT')
    - Auto-repairs corrupted / zeroed-out mask images (Detail_AO, Detail_Packed, ILM)
    - Rebuilds shader graph with Cel AO floor clamping ([0.35, 1.0])
    - Repacks Detail and ILM maps safely
    """
    if not mat:
        return False
    mat.use_nodes = True
    
    # 1. Detect or preserve shader role
    sh_type = mat.get("genos_shader_type")
    if not sh_type or sh_type not in {'DEFAULT', 'FACE', 'HAIR', 'METALLIC'}:
        sh_type = detect_shader_type_for_material(mat)
    mat["genos_shader_type"] = sh_type

    size = scene_texture_size()
    base_name = material_base_name(mat)

    # 2. Extract existing real textures (never lose user's albedo / normal)
    extracted_source = extract_source_textures_from_material(mat)

    def get_existing_img(node_name):
        node = mat.node_tree.nodes.get(node_name)
        if node and hasattr(node, 'image') and is_valid_image(node.image):
            return node.image
        return None

    base_img = (
        get_existing_img("BaseColor")
        or getattr(mat, "genos_base_color_map", None)
        or extracted_source.get("basecolor")
    )
    if not is_valid_image(base_img):
        # Scan node tree for any image texture node containing color / albedo
        for n in mat.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and hasattr(n, 'image') and is_valid_image(n.image):
                low = (n.name + " " + (n.label or "")).lower()
                if any(k in low for k in ("diff", "color", "albedo", "main", "base")):
                    base_img = n.image
                    break
    if not is_valid_image(base_img):
        base_img = make_image(f"{base_name}_BaseColor", size, size, alpha=True, colorspace="sRGB", color=(0.8, 0.8, 0.8, 1.0))
    set_image_colorspace(base_img, "sRGB")

    normal_img = (
        getattr(mat, "genos_normal_map", None)
        or get_existing_img("Normal_Tex")
        or extracted_source.get("normal_map")
    )
    if not is_valid_image(normal_img):
        for n in mat.node_tree.nodes:
            if n.type == 'NORMAL_MAP':
                for inp in n.inputs:
                    for link_in in inp.links:
                        from_node = link_in.from_node
                        if from_node.type == 'TEX_IMAGE' and is_valid_image(getattr(from_node, 'image', None)):
                            normal_img = from_node.image
                            break
                    if normal_img: break
                if normal_img: break
    if is_valid_image(normal_img):
        set_image_colorspace(normal_img, MASK_COLORSPACE)

    emission_img = (
        get_existing_img("Emission Map")
        or getattr(mat, "genos_emission_map", None)
        or extracted_source.get("emission_map")
    )
    if is_valid_image(emission_img):
        set_image_colorspace(emission_img, "sRGB")
    else:
        emission_img = make_image(f"{base_name}_EmissionMap", size, size, alpha=True, colorspace="sRGB", color=(0.0, 0.0, 0.0, 1.0))

    def existing_optional_map(key, prop_name, node_name, kind):
        img = getattr(mat, prop_name, None) or get_existing_img(node_name) or extracted_source.get(key)
        if is_valid_image(img):
            configure_standard_map_image(img, kind)
            return img
        return None

    roughness_img = existing_optional_map('roughness_map', 'genos_roughness_map', 'Roughness Map', 'ROUGHNESS')
    metallic_img = existing_optional_map('metallic_map', 'genos_metallic_map', 'Metallic Map', 'METALLIC')
    opacity_img = existing_optional_map('opacity_map', 'genos_opacity_map', 'Opacity Map', 'OPACITY')
    ao_map_img = existing_optional_map('ao_map', 'genos_ao_map', 'AO Map', 'AO')

    # 3. Handle masks with corruption auto-repair
    # Detail AO: If existing image is completely black (<= 0.004), repair to 1.0 white
    detail_ao_img = (
        get_existing_img("Detail_AO")
        or bpy.data.images.get(f"{base_name}_Detail_AOSrc")
    )
    if not is_valid_image(detail_ao_img):
        detail_ao_img = make_image(f"{base_name}_Detail_AOSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(1.0, 1.0, 1.0, 1.0))
    elif image_is_nearly_black(detail_ao_img):
        fill_image_solid(detail_ao_img, (1.0, 1.0, 1.0, 1.0), size, size)
    set_image_colorspace(detail_ao_img, MASK_COLORSPACE)

    # ILM Shadow: If existing image is completely black, repair to 0.5 neutral shadow
    ilm_shadow_img = (
        get_existing_img("ILM_Shadow")
        or bpy.data.images.get(f"{base_name}_ILM_ShadowSrc")
    )
    if not is_valid_image(ilm_shadow_img):
        ilm_shadow_img = make_image(f"{base_name}_ILM_ShadowSrc", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=(0.5, 0.5, 0.5, 1.0))
    elif image_is_nearly_black(ilm_shadow_img):
        fill_image_solid(ilm_shadow_img, (0.5, 0.5, 0.5, 1.0), size, size)
    set_image_colorspace(ilm_shadow_img, MASK_COLORSPACE)

    def get_or_make_mask(node_name, suffix, default_col):
        img = get_existing_img(node_name) or bpy.data.images.get(f"{base_name}_{suffix}")
        if not is_valid_image(img):
            img = make_image(f"{base_name}_{suffix}", size, size, alpha=True, colorspace=MASK_COLORSPACE, color=default_col)
        set_image_colorspace(img, MASK_COLORSPACE)
        return img

    ilm_emit_img = get_or_make_mask("ILM_Emission", "ILM_EmissionSrc", (0.0, 0.0, 0.0, 1.0))
    ilm_spec_img = get_or_make_mask("ILM_Spec", "ILM_SpecSrc", (0.0, 0.0, 0.0, 1.0))
    ilm_rim_img = get_or_make_mask("ILM_Rim", "ILM_RimSrc", (0.0, 0.0, 0.0, 1.0))

    detail_curve_img = get_or_make_mask("Detail_Curve", "Detail_CurveSrc", (0.0, 0.0, 0.0, 1.0))
    detail_accent_img = get_or_make_mask("Detail_Accent", "Detail_AccentSrc", (0.0, 0.0, 0.0, 1.0))
    detail_emission_img = get_or_make_mask("Detail_Emission", "Detail_EmissionSrc", (0.0, 0.0, 0.0, 1.0))
    pattern_mask_img = get_or_make_mask("Pattern Mask", "PatternMask", (0.0, 0.0, 0.0, 1.0))

    disp_img = getattr(mat, "genos_displacement_map", None) or get_existing_img("Displacement Map") or extracted_source.get('displacement_map') or bpy.data.images.get(f"{base_name}_Displacement")
    if not is_valid_image(disp_img):
        disp_img = make_image(f"{base_name}_Displacement", size, size, alpha=False, colorspace=MASK_COLORSPACE, color=(0.5, 0.5, 0.5, 1.0))
    set_image_colorspace(disp_img, MASK_COLORSPACE)

    sdf_img = None
    if sh_type == 'FACE':
        sdf_img = getattr(mat, "genos_sdf_map", None) or get_existing_img("SDF Map") or bpy.data.images.get(f"{base_name}_SDFMap")
        if not is_valid_image(sdf_img):
            sdf_img = make_image(f"{base_name}_SDFMap", size, size, alpha=False, colorspace=MASK_COLORSPACE, color=(0.5, 0.5, 0.5, 1.0))
        set_image_colorspace(sdf_img, MASK_COLORSPACE)

    images_dict = {
        "basecolor": base_img,
        "normal_map": normal_img,
        "emission_map": emission_img,
        "roughness_map": roughness_img,
        "metallic_map": metallic_img,
        "opacity_map": opacity_img,
        "ao_map": ao_map_img,
        "ilm_shadow": ilm_shadow_img,
        "ilm_emission": ilm_emit_img,
        "ilm_spec": ilm_spec_img,
        "ilm_rim": ilm_rim_img,
        "detail_ao": detail_ao_img,
        "detail_curve": detail_curve_img,
        "detail_accent": detail_accent_img,
        "detail_emission": detail_emission_img,
        "pattern_mask": pattern_mask_img,
        "displacement_map": disp_img,
        "sdf_map": sdf_img,
        "pattern_color": getattr(mat, "genos_pattern_color_map", None),
        "pattern_roughness": getattr(mat, "genos_pattern_roughness_map", None),
        "pattern_normal": getattr(mat, "genos_pattern_normal_map", None),
    }

    # Ensure packed textures exist and are not corrupted black
    det_packed = bpy.data.images.get(f"{base_name}_Detail")
    if det_packed and image_is_nearly_black(det_packed):
        fill_image_solid(det_packed, (1.0, 0.0, 0.0, 1.0), size, size)
    ilm_packed = bpy.data.images.get(f"{base_name}_ILM")
    if ilm_packed and image_is_nearly_black(ilm_packed):
        fill_image_solid(ilm_packed, (0.5, 0.0, 0.0, 1.0), size, size)

    # Protect image users
    for img in images_dict.values():
        if img:
            try: img.use_fake_user = True
            except Exception: pass

    # If already-exported packed ILM/Detail maps exist, resolve them
    try:
        try_load_packed_maps_into_images(mat, images_dict)
    except Exception:
        pass

    is_baked = "is_anime_toon_baked" in mat
    if is_baked:
        det_packed = det_packed or packed_image_for_material(mat, "Detail", (1.0, 0.0, 0.0, 1.0))
        ilm_packed = ilm_packed or packed_image_for_material(mat, "ILM", (0.5, 0.0, 0.0, 1.0))
        build_baked_material(
            mat,
            base_img=base_img,
            emit_img=emission_img,
            nmap_img=normal_img,
            ilm_img=ilm_packed,
            det_img=det_packed,
            sdf_img=sdf_img,
            disp_img=disp_img,
            pattern_mask_img=pattern_mask_img,
            pattern_color_img=getattr(mat, "genos_pattern_color_map", None),
            roughness_img=roughness_img,
            metallic_img=metallic_img,
            opacity_img=opacity_img,
            ao_img=ao_map_img
        )
    else:
        build_preview_material(mat, images_dict)

    # Re-assign custom property image pointers that might have detached during nodes.clear()
    try:
        mat.genos_base_color_map = base_img
        mat.genos_emission_map = emission_img
        if normal_img:
            mat.genos_normal_map = normal_img
        if roughness_img:
            mat.genos_roughness_map = roughness_img
        if metallic_img:
            mat.genos_metallic_map = metallic_img
        if opacity_img:
            mat.genos_opacity_map = opacity_img
        if ao_map_img:
            mat.genos_ao_map = ao_map_img
        if disp_img:
            mat.genos_displacement_map = disp_img
        if sdf_img:
            mat.genos_sdf_map = sdf_img
        if det_packed:
            mat.genos_detail_packed = det_packed
        if ilm_packed:
            mat.genos_ilm_packed = ilm_packed
    except Exception:
        pass

    # Repack Detail and ILM maps with safe packing
    pack_material_detail(mat)
    pack_material_ilm(mat)
    return True

class GENOS_OT_regenerate_shader(bpy.types.Operator):
    bl_idname = "genos.regenerate_shader"
    bl_label = "Regenerate Node Tree"

    def execute(self, context):
        obj = context.active_object
        if not obj or not obj.active_material: return {'CANCELLED'}
        mat = obj.active_material

        success = adapt_material_to_new_scheme(mat)
        if success:
            self.report({'INFO'}, f"Failsafe Cleaned & Regenerated Node Tree for '{mat.name}'.")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, f"Failed to regenerate shader for '{mat.name}'.")
            return {'CANCELLED'}

class GENOS_OT_adapt_all_shaders(bpy.types.Operator):
    bl_idname = "genos.adapt_all_shaders"
    bl_label = "Adapt All Shaders to New Scheme"
    bl_description = "Scans all materials on the model, auto-detects roles, auto-repairs corrupted/black masks, retains real textures, and rebuilds shader trees to the robust cel scheme"

    def execute(self, context):
        targets = []
        seen_mats = set()

        # 1. Gather all materials from active and selected mesh objects
        objs = list(context.selected_objects) if context.selected_objects else []
        if context.active_object and context.active_object not in objs:
            objs.append(context.active_object)

        for o in objs:
            if o.type == 'MESH':
                for slot in o.material_slots:
                    if slot.material and slot.material not in seen_mats:
                        seen_mats.add(slot.material)
                        targets.append(slot.material)

        # If no mesh objects selected, scan all materials in current scene/file
        if not targets:
            for mat in bpy.data.materials:
                if mat.use_nodes and ("is_anime_toon" in mat or "is_anime_toon_baked" in mat):
                    if mat not in seen_mats:
                        seen_mats.add(mat)
                        targets.append(mat)

        if not targets:
            self.report({'WARNING'}, "No mesh materials found to adapt.")
            return {'CANCELLED'}

        count = 0
        for mat in targets:
            try:
                if adapt_material_to_new_scheme(mat):
                    count += 1
            except Exception as e:
                print(f"Error adapting material '{mat.name}': {e}")

        self.report({'INFO'}, f"Successfully adapted and repaired {count} material(s) to the new scheme.")
        return {'FINISHED'}

ANIME_HAIR_COLOR_PALETTES = {
    "NIKKE_RED": {
        "band1": 0.47, "band2": 0.20, "feather": 0.020,
        "shadow1": (0.76, 0.50, 0.58, 1.0),
        "shadow2": (0.42, 0.20, 0.30, 1.0),
        "highlight": (1.0, 0.94, 0.97, 1.0),
        "tip": (0.72, 0.18, 0.36, 1.0),
        "tip_str": 0.22,
        "spec_str": 0.34, "halo_pos": 0.69, "halo_w": 0.050, "strands_str": 0.08, "strands_sc": 20.0,
    },
    "BLONDE": {
        "band1": 0.25, "band2": 0.12, "feather": 0.040,
        "shadow1": (0.88, 0.68, 0.45, 1.0),
        "shadow2": (0.65, 0.42, 0.22, 1.0),
        "highlight": (1.0, 0.96, 0.82, 1.0),
        "tip": (0.80, 0.52, 0.30, 1.0),
        "tip_str": 0.25,
        "spec_str": 0.90, "halo_pos": 0.50, "halo_w": 0.15, "strands_str": 0.70, "strands_sc": 28.0,
    },
    "SILVER_WHITE": {
        "band1": 0.30, "band2": 0.15, "feather": 0.050,
        "shadow1": (0.78, 0.76, 0.88, 1.0),
        "shadow2": (0.52, 0.48, 0.68, 1.0),
        "highlight": (0.98, 0.98, 1.0, 1.0),
        "tip": (0.65, 0.62, 0.80, 1.0),
        "tip_str": 0.28,
        "spec_str": 0.80, "halo_pos": 0.48, "halo_w": 0.18, "strands_str": 0.60, "strands_sc": 22.0,
    },
    "RAVEN_BLACK": {
        "band1": 0.22, "band2": 0.10, "feather": 0.040,
        "shadow1": (0.42, 0.42, 0.55, 1.0),
        "shadow2": (0.22, 0.22, 0.32, 1.0),
        "highlight": (0.78, 0.85, 0.98, 1.0),
        "tip": (0.28, 0.25, 0.40, 1.0),
        "tip_str": 0.20,
        "spec_str": 0.95, "halo_pos": 0.50, "halo_w": 0.14, "strands_str": 0.75, "strands_sc": 32.0,
    },
    "SAKURA_PINK": {
        "band1": 0.26, "band2": 0.12, "feather": 0.045,
        "shadow1": (0.88, 0.50, 0.68, 1.0),
        "shadow2": (0.62, 0.25, 0.45, 1.0),
        "highlight": (1.0, 0.90, 0.95, 1.0),
        "tip": (0.75, 0.25, 0.50, 1.0),
        "tip_str": 0.30,
        "spec_str": 0.85, "halo_pos": 0.50, "halo_w": 0.16, "strands_str": 0.65, "strands_sc": 24.0,
    },
    "AZURE_BLUE": {
        "band1": 0.28, "band2": 0.14, "feather": 0.045,
        "shadow1": (0.55, 0.65, 0.88, 1.0),
        "shadow2": (0.28, 0.38, 0.65, 1.0),
        "highlight": (0.85, 0.95, 1.0, 1.0),
        "tip": (0.20, 0.30, 0.55, 1.0),
        "tip_str": 0.32,
        "spec_str": 0.85, "halo_pos": 0.50, "halo_w": 0.16, "strands_str": 0.65, "strands_sc": 26.0,
    },
    "EMERALD_GREEN": {
        "band1": 0.26, "band2": 0.12, "feather": 0.045,
        "shadow1": (0.48, 0.78, 0.68, 1.0),
        "shadow2": (0.22, 0.52, 0.45, 1.0),
        "highlight": (0.88, 1.0, 0.92, 1.0),
        "tip": (0.20, 0.45, 0.40, 1.0),
        "tip_str": 0.28,
        "spec_str": 0.85, "halo_pos": 0.50, "halo_w": 0.16, "strands_str": 0.65, "strands_sc": 24.0,
    },
    "BRUNETTE_BROWN": {
        "band1": 0.26, "band2": 0.12, "feather": 0.045,
        "shadow1": (0.72, 0.48, 0.38, 1.0),
        "shadow2": (0.45, 0.25, 0.18, 1.0),
        "highlight": (1.0, 0.88, 0.75, 1.0),
        "tip": (0.50, 0.25, 0.20, 1.0),
        "tip_str": 0.25,
        "spec_str": 0.85, "halo_pos": 0.50, "halo_w": 0.15, "strands_str": 0.65, "strands_sc": 24.0,
    },
}

class GENOS_OT_add_hair_band(bpy.types.Operator):
    bl_idname = 'genos.add_hair_band'
    bl_label = 'Add Hair Highlight Band'
    bl_description = 'Add a fully independent realtime highlight band'
    def execute(self, context):
        scene = context.scene; _ensure_dynamic_hair_bands(scene); bands = scene.genos_hair_bands
        global _GENOS_HAIR_BAND_BATCH_UPDATE
        _GENOS_HAIR_BAND_BATCH_UPDATE = True
        try:
            prev = bands[-1] if len(bands) else None
            spec = _hair_band_specs(scene)[-1] if prev else {
                'coord_mode':'GENERATED_Z','position':0.65,'width':0.05,'strength':1.0,'opacity':1.0,
                'emission_enabled':True,'emission_strength':0.60,'color':(1.0,0.95,0.98,1.0),
                'softness':0.22,'blur':0.18,'curve_center':0.5,'curvature':0.18,'spot_density':9.0,'spot_gap':0.22,
                'spot_shape':'ROUND','spot_aspect':1.0,'strand_detail':0.35,'enabled':True}
            spec = dict(spec); spec['label'] = 'Band %d' % (len(bands)+1)
            if prev: spec['position'] = prev.position - max(0.055, prev.width * 2.0); spec['strength'] = max(0.20, prev.strength * 0.82)
            _assign_hair_band_from_spec(bands.add(), spec, len(bands))
            scene.genos_hair_band_index = len(bands)-1; scene.genos_hair_halo_count = min(128, max(1, len(bands)))
        finally: _GENOS_HAIR_BAND_BATCH_UPDATE = False
        rebuilt = _rebuild_dynamic_hair_materials(context); _update_hair_halo_live(scene, context)
        self.report({'INFO'}, 'Added independent highlight band; rebuilt %d hair material(s).' % rebuilt); return {'FINISHED'}


class GENOS_OT_remove_hair_band(bpy.types.Operator):
    bl_idname = 'genos.remove_hair_band'; bl_label = 'Remove Hair Highlight Band'
    index: IntProperty(default=-1)
    def execute(self, context):
        scene=context.scene; bands=scene.genos_hair_bands; idx=self.index if self.index>=0 else scene.genos_hair_band_index
        if len(bands)<=1: self.report({'WARNING'}, 'At least one highlight band must remain.'); return {'CANCELLED'}
        if idx<0 or idx>=len(bands): return {'CANCELLED'}
        bands.remove(idx); scene.genos_hair_band_index=min(idx,len(bands)-1); scene.genos_hair_halo_count=min(128,max(1,len(bands)))
        rebuilt=_rebuild_dynamic_hair_materials(context); _update_hair_halo_live(scene,context)
        self.report({'INFO'}, 'Removed highlight band; rebuilt %d hair material(s).' % rebuilt); return {'FINISHED'}


class GENOS_OT_duplicate_hair_band(bpy.types.Operator):
    bl_idname='genos.duplicate_hair_band'; bl_label='Duplicate Hair Highlight Band'
    index: IntProperty(default=-1)
    def execute(self, context):
        scene=context.scene; bands=scene.genos_hair_bands; idx=self.index if self.index>=0 else scene.genos_hair_band_index
        if idx<0 or idx>=len(bands): return {'CANCELLED'}
        spec=dict(_hair_band_specs(scene)[idx]); spec['label']='Band %d' % (len(bands)+1); spec['position']-=max(0.045,spec['width']*1.7)
        global _GENOS_HAIR_BAND_BATCH_UPDATE; _GENOS_HAIR_BAND_BATCH_UPDATE=True
        try: _assign_hair_band_from_spec(bands.add(), spec, len(bands)); scene.genos_hair_band_index=len(bands)-1; scene.genos_hair_halo_count=min(128,len(bands))
        finally: _GENOS_HAIR_BAND_BATCH_UPDATE=False
        rebuilt=_rebuild_dynamic_hair_materials(context); _update_hair_halo_live(scene,context)
        self.report({'INFO'}, 'Duplicated highlight band; rebuilt %d hair material(s).' % rebuilt); return {'FINISHED'}


class GENOS_OT_move_hair_band(bpy.types.Operator):
    bl_idname='genos.move_hair_band'; bl_label='Move Hair Highlight Band'
    index:IntProperty(default=-1); direction:IntProperty(default=1)
    def execute(self,context):
        scene=context.scene; bands=scene.genos_hair_bands; idx=self.index if self.index>=0 else scene.genos_hair_band_index; target=idx+(1 if self.direction>0 else -1)
        if idx<0 or idx>=len(bands) or target<0 or target>=len(bands): return {'CANCELLED'}
        bands.move(idx,target); scene.genos_hair_band_index=target; rebuilt=_rebuild_dynamic_hair_materials(context); _update_hair_halo_live(scene,context)
        self.report({'INFO'}, 'Reordered highlight bands; rebuilt %d hair material(s).' % rebuilt); return {'FINISHED'}


class GENOS_OT_add_hair_toon_band(bpy.types.Operator):
    bl_idname='genos.add_hair_toon_band'; bl_label='Add Hair Toon Band'
    bl_description='Add another independent cel-shadow region'
    def execute(self,context):
        scene=context.scene; _ensure_dynamic_hair_toon_bands(scene); bands=scene.genos_hair_toon_bands
        global _GENOS_HAIR_TOON_BATCH_UPDATE; _GENOS_HAIR_TOON_BATCH_UPDATE=True
        try:
            prev=bands[-1] if len(bands) else None; band=bands.add(); band.label='Toon Band %d' % len(bands)
            if prev:
                band.threshold=max(0.0,prev.threshold-0.14); band.feather=prev.feather; band.strength=prev.strength
                c=tuple(prev.color); band.color=(max(0.0,c[0]*0.78),max(0.0,c[1]*0.78),max(0.0,c[2]*0.82),1.0)
            else:
                band.threshold=0.45; band.feather=0.02; band.strength=1.0; band.color=(0.72,0.50,0.58,1.0)
            scene.genos_hair_toon_band_index=len(bands)-1
        finally: _GENOS_HAIR_TOON_BATCH_UPDATE=False
        rebuilt=_rebuild_dynamic_hair_materials(context); _update_hair_toon_live(scene,context)
        self.report({'INFO'}, 'Added toon band; rebuilt %d hair material(s).' % rebuilt); return {'FINISHED'}


class GENOS_OT_remove_hair_toon_band(bpy.types.Operator):
    bl_idname='genos.remove_hair_toon_band'; bl_label='Remove Hair Toon Band'
    index:IntProperty(default=-1)
    def execute(self,context):
        scene=context.scene; bands=scene.genos_hair_toon_bands; idx=self.index if self.index>=0 else scene.genos_hair_toon_band_index
        if len(bands)<=1: self.report({'WARNING'}, 'At least one toon band must remain.'); return {'CANCELLED'}
        if idx<0 or idx>=len(bands): return {'CANCELLED'}
        bands.remove(idx); scene.genos_hair_toon_band_index=min(idx,len(bands)-1); rebuilt=_rebuild_dynamic_hair_materials(context); _update_hair_toon_live(scene,context)
        self.report({'INFO'}, 'Removed toon band; rebuilt %d hair material(s).' % rebuilt); return {'FINISHED'}


class GENOS_OT_duplicate_hair_toon_band(bpy.types.Operator):
    bl_idname='genos.duplicate_hair_toon_band'; bl_label='Duplicate Hair Toon Band'
    index:IntProperty(default=-1)
    def execute(self,context):
        scene=context.scene; bands=scene.genos_hair_toon_bands; idx=self.index if self.index>=0 else scene.genos_hair_toon_band_index
        if idx<0 or idx>=len(bands): return {'CANCELLED'}
        src=bands[idx]; global _GENOS_HAIR_TOON_BATCH_UPDATE; _GENOS_HAIR_TOON_BATCH_UPDATE=True
        try:
            b=bands.add(); b.label='Toon Band %d' % len(bands); b.threshold=max(0.0,src.threshold-0.06); b.feather=src.feather; b.strength=src.strength; b.color=tuple(src.color); scene.genos_hair_toon_band_index=len(bands)-1
        finally: _GENOS_HAIR_TOON_BATCH_UPDATE=False
        rebuilt=_rebuild_dynamic_hair_materials(context); _update_hair_toon_live(scene,context)
        self.report({'INFO'}, 'Duplicated toon band; rebuilt %d hair material(s).' % rebuilt); return {'FINISHED'}


class GENOS_OT_move_hair_toon_band(bpy.types.Operator):
    bl_idname='genos.move_hair_toon_band'; bl_label='Move Hair Toon Band'
    index:IntProperty(default=-1); direction:IntProperty(default=1)
    def execute(self,context):
        scene=context.scene; bands=scene.genos_hair_toon_bands; idx=self.index if self.index>=0 else scene.genos_hair_toon_band_index; target=idx+(1 if self.direction>0 else -1)
        if idx<0 or idx>=len(bands) or target<0 or target>=len(bands): return {'CANCELLED'}
        bands.move(idx,target); scene.genos_hair_toon_band_index=target; rebuilt=_rebuild_dynamic_hair_materials(context); _update_hair_toon_live(scene,context)
        self.report({'INFO'}, 'Reordered toon bands; rebuilt %d hair material(s).' % rebuilt); return {'FINISHED'}


class GENOS_OT_apply_hair_preset(bpy.types.Operator):
    bl_idname = "genos.apply_hair_preset"
    bl_label = "Apply Hair Color Preset"
    bl_description = "Applies calibrated 2D anime hair thresholds, shadow multipliers, and highlight glazes for the selected hair color preset"

    preset: bpy.props.StringProperty(default="")

    def execute(self, context):
        scene = context.scene
        preset_key = self.preset or getattr(scene, "genos_hair_color_preset", "NIKKE_RED")
        p = ANIME_HAIR_COLOR_PALETTES.get(preset_key, ANIME_HAIR_COLOR_PALETTES["NIKKE_RED"])

        scene.genos_hair_color_preset = preset_key
        scene.genos_hair_source_preservation = 0.15 if preset_key == 'NIKKE_RED' else 0.35
        scene.genos_hair_halo_coord_mode = 'GENERATED_Z'

        # Presets now populate the dynamic band collection instead of being limited to three hard-coded bands.
        if preset_key == 'NIKKE_RED':
            scene.genos_hair_halo_softness = 0.24
            scene.genos_hair_halo_curve_center = 0.50
            scene.genos_hair_halo_curvature = 0.24
            scene.genos_hair_halo_blur = 0.20
            scene.genos_hair_halo_spot_density = 8.5
            scene.genos_hair_halo_spot_gap = 0.18
            scene.genos_hair_halo_spot_shape = 'ROUND'
            scene.genos_hair_halo_spot_aspect = 1.0
            _set_dynamic_hair_bands(scene, [
                {'label': 'Primary White-Pink', 'enabled': True, 'coord_mode':'GENERATED_Z', 'position':0.69, 'width':0.050, 'strength':1.0, 'opacity':0.92, 'emission_enabled':True, 'emission_strength':0.72, 'color':(1.0,0.97,0.985,1.0), 'softness':0.24, 'blur':0.20, 'curve_center':0.50, 'curvature':0.24, 'spot_density':8.5, 'spot_gap':0.18, 'spot_shape':'ROUND', 'spot_aspect':1.0, 'strand_detail':0.18},
                {'label': 'Secondary Pink', 'enabled': True, 'coord_mode':'GENERATED_Z', 'position':0.60, 'width':0.028, 'strength':0.62, 'opacity':0.78, 'emission_enabled':True, 'emission_strength':0.38, 'color':(1.0,0.72,0.82,1.0), 'softness':0.28, 'blur':0.22, 'curve_center':0.50, 'curvature':0.22, 'spot_density':8.5, 'spot_gap':0.20, 'spot_shape':'ROUND', 'spot_aspect':1.0, 'strand_detail':0.10},
            ])
        else:
            # Generic presets start with two editable rows and can be extended indefinitely with Add Band.
            hi = tuple(p['highlight'])
            second = (min(1.0, hi[0] * 0.95), min(1.0, hi[1] * 0.88), min(1.0, hi[2] * 0.90), 1.0)
            _set_dynamic_hair_bands(scene, [
                {'label': 'Primary Highlight', 'enabled': True, 'position': p['halo_pos'], 'width': p['halo_w'], 'strength': 1.0, 'opacity': 1.0, 'emission_enabled': True, 'emission_strength': 0.50, 'color': hi},
                {'label': 'Secondary Highlight', 'enabled': True, 'position': p['halo_pos'] - max(0.06, p['halo_w'] * 0.75), 'width': max(0.018, p['halo_w'] * 0.60), 'strength': 0.48, 'opacity': 0.85, 'emission_enabled': True, 'emission_strength': 0.28, 'color': second},
            ])
        scene.genos_hair_band1_thresh = p["band1"]
        scene.genos_hair_band2_thresh = p["band2"]
        scene.genos_hair_feather = p["feather"]
        scene.genos_hair_shadow_color_1 = p["shadow1"]
        scene.genos_hair_shadow_color_2 = p["shadow2"]
        _set_dynamic_hair_toon_bands(scene, [
            {'label': 'Mid Shadow', 'threshold': p['band1'], 'feather': p['feather'], 'strength': 1.0, 'color': p['shadow1']},
            {'label': 'Deep Shadow', 'threshold': p['band2'], 'feather': p['feather'], 'strength': 1.0, 'color': p['shadow2']},
        ])
        scene.genos_hair_highlight_tint = p["highlight"]
        scene.genos_hair_tip_tint = p["tip"]
        scene.genos_hair_tip_strength = 0.0 if preset_key == "NIKKE_RED" else p["tip_str"]
        scene.genos_hair_pbr_spec_str = 0.0
        scene.genos_hair_highlight_strength = 0.68 if preset_key == "NIKKE_RED" else p["spec_str"]
        scene.genos_hair_halo_pos = p["halo_pos"]
        scene.genos_hair_halo_width = p["halo_w"]
        scene.genos_hair_strands_strength = p["strands_str"]
        scene.genos_hair_strands_scale = p["strands_sc"]
        scene.genos_terminator_fringe_intensity = 0.25 if preset_key == "NIKKE_RED" else 0.35

        adapted_count = 0
        obj = context.active_object
        if obj and obj.type == 'MESH':
            for slot in obj.material_slots:
                mat = slot.material
                if mat and (mat.get("genos_shader_type") == 'HAIR' or "hair" in mat.name.lower()):
                    adapt_material_to_new_scheme(mat)
                    adapted_count += 1
        if adapted_count == 0:
            for mat in bpy.data.materials:
                if mat.use_nodes and (mat.get("genos_shader_type") == 'HAIR' or "hair" in mat.name.lower()):
                    adapt_material_to_new_scheme(mat)
                    adapted_count += 1

        self.report({'INFO'}, f"Applied Hair Preset '{preset_key}' ({adapted_count} material(s) updated).")
        return {'FINISHED'}

class GENOS_OT_apply_hair_preset_nikke(bpy.types.Operator):
    bl_idname = "genos.apply_hair_preset_nikke"
    bl_label = "Match 2D Reference (Nikke Red)"
    bl_description = "Applies calibrated Nikke red hair settings matching the supplied reference more closely: bright pink-red base hair, pale near-white crown highlights, soft rounded pink highlight spots, richer ruby cel shadows, and a fully real-time editable halo setup"

    def execute(self, context):
        bpy.ops.genos.apply_hair_preset(preset="NIKKE_RED")
        return {'FINISHED'}

class GENOS_OT_auto_harmonize_hair_color(bpy.types.Operator):
    bl_idname = "genos.auto_harmonize_hair_color"
    bl_label = "Auto-Harmonize Hair Color"
    bl_description = "Samples the active hair material's base albedo texture and procedurally calculates harmonious 2D anime cel shadow tints and highlights"

    def execute(self, context):
        obj = context.active_object
        if not obj or not obj.active_material:
            self.report({'WARNING'}, "No active mesh or material selected.")
            return {'CANCELLED'}

        mat = obj.active_material
        base_img = getattr(mat, "genos_base_color_map", None)
        avg_r, avg_g, avg_b = 0.8, 0.3, 0.3

        if mat.use_nodes:
            if not base_img:
                b_node = mat.node_tree.nodes.get("BaseColor")
                if b_node and hasattr(b_node, "image") and b_node.image:
                    base_img = b_node.image
                else:
                    for n in mat.node_tree.nodes:
                        if n.type == 'TEX_IMAGE' and getattr(n, "image", None):
                            base_img = n.image
                            break
                        elif n.type == 'BSDF_PRINCIPLED' and "Base Color" in n.inputs:
                            try:
                                col = n.inputs["Base Color"].default_value
                                avg_r, avg_g, avg_b = float(col[0]), float(col[1]), float(col[2])
                            except Exception:
                                pass

        if base_img and hasattr(base_img, "pixels") and len(base_img.pixels) >= 4:
            try:
                # Reshape to (num_pixels, 4) first so RGBA channels are never desynced
                px = np.array(base_img.pixels[:], dtype=np.float32).reshape(-1, 4)
                step = max(1, len(px) // 2000)
                sampled = px[::step]
                valid = sampled[sampled[:, 3] > 0.1]
                if len(valid) > 0:
                    avg_r = float(np.mean(valid[:, 0]))
                    avg_g = float(np.mean(valid[:, 1]))
                    avg_b = float(np.mean(valid[:, 2]))
            except Exception as e:
                print(f"[Anime Studio] Pixel sampling fallback: {e}")

        import colorsys
        h, s, v = colorsys.rgb_to_hsv(min(1.0, max(0.0, avg_r)), min(1.0, max(0.0, avg_g)), min(1.0, max(0.0, avg_b)))
        
        # Flattering anime color harmony
        sh1_h = (h + 0.035) % 1.0
        sh1_s = min(1.0, s * 1.35 + 0.10)
        sh1_v = max(0.20, v * 0.70)
        sh1_r, sh1_g, sh1_b = colorsys.hsv_to_rgb(sh1_h, sh1_s, sh1_v)

        sh2_h = (h + 0.065) % 1.0
        sh2_s = min(1.0, s * 1.50 + 0.15)
        sh2_v = max(0.12, v * 0.42)
        sh2_r, sh2_g, sh2_b = colorsys.hsv_to_rgb(sh2_h, sh2_s, sh2_v)

        hl_h = (h - 0.02) % 1.0
        hl_s = max(0.10, s * 0.35)
        hl_v = min(1.0, max(0.85, v + 0.25))
        hl_r, hl_g, hl_b = colorsys.hsv_to_rgb(hl_h, hl_s, hl_v)

        scene = context.scene
        scene.genos_hair_shadow_color_1 = (sh1_r, sh1_g, sh1_b, 1.0)
        scene.genos_hair_shadow_color_2 = (sh2_r, sh2_g, sh2_b, 1.0)
        scene.genos_hair_highlight_tint = (hl_r, hl_g, hl_b, 1.0)
        scene.genos_hair_pbr_spec_str = 0.0

        adapt_material_to_new_scheme(mat)
        self.report({'INFO'}, f"Auto-Harmonized hair colors from texture (RGB: {avg_r:.2f}, {avg_g:.2f}, {avg_b:.2f}).")
        return {'FINISHED'}

class GENOS_OT_detect_and_apply_hair_ombre(bpy.types.Operator):
    bl_idname = "genos.detect_and_apply_hair_ombre"
    bl_label = "Detect & Apply Hair Ombre"
    bl_description = "Analyze 3D hair mesh geometry, autodetect hair tips and boundary edges across clumps/islands, and generate a 'Hair_Ombre' color attribute"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        targets = [o for o in context.selected_objects if o.type == 'MESH']
        if not targets and context.active_object and context.active_object.type == 'MESH':
            targets = [context.active_object]

        if not targets:
            self.report({'WARNING'}, "Please select at least one hair mesh object.")
            return {'CANCELLED'}

        orig_mode = context.object.mode if context.object else 'OBJECT'
        if orig_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass

        total_islands = 0
        total_verts = 0

        for obj in targets:
            mesh = obj.data
            n_verts = len(mesh.vertices)
            if n_verts == 0:
                continue

            # Build vertex adjacency graph to find connected components (islands/clumps)
            adj = [[] for _ in range(n_verts)]
            for edge in mesh.edges:
                v0, v1 = edge.vertices
                adj[v0].append(v1)
                adj[v1].append(v0)

            # Find boundary edges (edges used by only 1 polygon)
            edge_face_count = {}
            for poly in mesh.polygons:
                for ek in poly.edge_keys:
                    edge_face_count[ek] = edge_face_count.get(ek, 0) + 1
            boundary_verts = set()
            for (v0, v1), count in edge_face_count.items():
                if count == 1:
                    boundary_verts.add(v0)
                    boundary_verts.add(v1)

            # BFS to find connected components
            visited = [False] * n_verts
            vert_factors = [0.0] * n_verts

            for start_idx in range(n_verts):
                if visited[start_idx]:
                    continue
                island = []
                queue = [start_idx]
                visited[start_idx] = True
                while queue:
                    curr = queue.pop()
                    island.append(curr)
                    for neighbor in adj[curr]:
                        if not visited[neighbor]:
                            visited[neighbor] = True
                            queue.append(neighbor)

                total_islands += 1

                # Calculate local bounds for this hair clump
                z_vals = [mesh.vertices[vi].co.z for vi in island]
                z_min = min(z_vals)
                z_max = max(z_vals)
                dz = z_max - z_min

                x_vals = [mesh.vertices[vi].co.x for vi in island]
                dx = max(x_vals) - min(x_vals)
                y_vals = [mesh.vertices[vi].co.y for vi in island]
                dy = max(y_vals) - min(y_vals)

                # For each vertex in this island, calculate root (0.0) -> tip (1.0)
                for vi in island:
                    v_co = mesh.vertices[vi].co
                    if dz > 0.002:
                        # Standard vertical hair strand (bangs, locks, ponytails)
                        t = (z_max - v_co.z) / dz
                    else:
                        # Horizontal strand or flat card: use distance along dominant axis
                        if dx >= dy and dx > 0.002:
                            t = (abs(v_co.x) - min(abs(mesh.vertices[k].co.x) for k in island)) / max(1e-4, dx)
                        elif dy > 0.002:
                            t = (abs(v_co.y) - min(abs(mesh.vertices[k].co.y) for k in island)) / max(1e-4, dy)
                        else:
                            t = 0.5

                    # If this vertex is on an open boundary edge at the lower end of the strand,
                    # boost to guarantee crisp 1.0 right at the hair tip edge
                    if vi in boundary_verts and t > 0.4:
                        t = min(1.0, t * 1.25)

                    vert_factors[vi] = max(0.0, min(1.0, t))

            # Store into Color Attribute 'Hair_Ombre' (domain='POINT', type='FLOAT_COLOR')
            ca = mesh.color_attributes.get("Hair_Ombre")
            if not ca:
                ca = mesh.color_attributes.new(name="Hair_Ombre", type='FLOAT_COLOR', domain='POINT')
            elif ca.domain != 'POINT' or ca.data_type != 'FLOAT_COLOR':
                mesh.color_attributes.remove(ca)
                ca = mesh.color_attributes.new(name="Hair_Ombre", type='FLOAT_COLOR', domain='POINT')

            for vi, val in enumerate(vert_factors):
                ca.data[vi].color = (val, val, val, 1.0)

            mesh.update()
            total_verts += n_verts

        if orig_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode=orig_mode)
            except Exception:
                pass

        context.scene.genos_hair_ombre_coord_mode = 'MESH'
        _rebuild_dynamic_hair_materials(context)

        self.report({'INFO'}, f"Hair Ombre applied! Detected {total_islands} hair clump(s) across {total_verts} vertices.")
        return {'FINISHED'}


class GENOS_OT_bake_hair_ombre(bpy.types.Operator):
    bl_idname = "genos.bake_hair_ombre"
    bl_label = "Bake Hair Ombre to Texture"
    bl_description = "Bake the hair ombre gradient directly into the hair's Base Color texture map or export an Ombre Mask texture"
    bl_options = {'REGISTER', 'UNDO'}

    bake_mode: EnumProperty(
        name="Bake Target",
        items=[
            ("BASE_COLOR", "Base Color with Ombre", "Bake hair base albedo with ombre gradient blended into texture"),
            ("MASK", "Ombre Mask Map", "Bake standalone grayscale ombre gradient mask map"),
        ],
        default="BASE_COLOR"
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "Please select a hair mesh object.")
            return {'CANCELLED'}

        mat = obj.active_material
        if not mat or not mat.use_nodes:
            self.report({'WARNING'}, "Active object has no node material.")
            return {'CANCELLED'}

        if not obj.data.uv_layers:
            self.report({'ERROR'}, "Mesh has no UV layers. Please unwrap the mesh before baking.")
            return {'CANCELLED'}

        scene = context.scene

        # Determine target image
        size = scene_texture_size()
        base_name = material_base_name(mat)

        if self.bake_mode == "BASE_COLOR":
            target_node_name = "Base Color Map"
            img_name = f"{base_name}_BaseColor_Ombre"
            colorspace = 'sRGB'
            prefill = (1.0, 1.0, 1.0, 1.0)
            target_img = bpy.data.images.get(img_name)
            if not target_img:
                target_img = make_image(img_name, size, size, alpha=True, colorspace=colorspace, color=prefill)
        else:
            target_node_name = "Detail_Accent"
            img_name = f"{base_name}_OmbreMask"
            colorspace = MASK_COLORSPACE
            prefill = (0.0, 0.0, 0.0, 1.0)
            target_img = bpy.data.images.get(img_name)
            if not target_img:
                target_img = make_image(img_name, size, size, alpha=True, colorspace=colorspace, color=prefill)

        # Build graph builder for baking
        def ombre_bake_graph(temp_nodes, temp_links, scn):
            if self.bake_mode == "BASE_COLOR":
                existing_base = None
                base_node = mat.node_tree.nodes.get("Base Color Map")
                
                src_img = None
                if base_node:
                    if "genos_orig_base" in mat:
                        src_img = bpy.data.images.get(mat["genos_orig_base"])
                    if not src_img:
                        src_img = getattr(base_node, "image", None)
                
                if src_img:
                    t_node = temp_nodes.new("ShaderNodeTexImage")
                    if src_img == target_img:
                        temp_img = src_img.copy()
                        temp_img.name = "TEMP_BAKE_COPY_" + target_img.name
                        t_node.image = temp_img
                    else:
                        t_node.image = src_img
                    t_node.location = (-900, 300)
                    existing_base = t_node.outputs["Color"]
                else:
                    col_node = temp_nodes.new("ShaderNodeRGB")
                    col_node.location = (-900, 300)
                    col_node.outputs[0].default_value = (0.9, 0.85, 0.8, 1.0)
                    existing_base = col_node.outputs[0]

                return _build_hair_tip_gradient(temp_nodes, temp_links, scn, existing_base, loc=(-500, 300))
            else:
                attr_node = make_node(temp_nodes, "ShaderNodeAttribute", "Hair Ombre Attribute", (-600, 0))
                attr_node.attribute_name = "Hair_Ombre"

                uv_node = make_node(temp_nodes, "ShaderNodeTexCoord", "Hair Tip UV", (-600, -200))
                sep_uv = make_node(temp_nodes, "ShaderNodeSeparateXYZ", "Hair Tip Sep UV", (-450, -200))
                temp_links.new(uv_node.outputs["UV"], sep_uv.inputs[0])
                inv_y = make_node(temp_nodes, "ShaderNodeMath", "Hair Tip Invert UV Y", (-300, -200))
                inv_y.operation = 'SUBTRACT'
                inv_y.inputs[0].default_value = 1.0
                temp_links.new(sep_uv.outputs["Y"], inv_y.inputs[1])

                combine_fac = make_node(temp_nodes, "ShaderNodeMath", "Hair Tip Factor Combine", (-300, 0))
                combine_fac.operation = 'MAXIMUM'
                temp_links.new(attr_node.outputs["Factor"], combine_fac.inputs[0])
                temp_links.new(inv_y.outputs[0], combine_fac.inputs[1])

                spread = float(getattr(scn, "genos_hair_ombre_range", 0.35))
                power = float(getattr(scn, "genos_hair_ombre_power", 1.5))
                tip_str = float(getattr(scn, "genos_hair_tip_strength", 0.85))

                from_min = max(0.0, 1.0 - spread)
                map_range = make_node(temp_nodes, "ShaderNodeMapRange", "Hair Ombre Map Range", (-100, 0))
                map_range.clamp = True
                map_range.inputs["From Min"].default_value = from_min
                map_range.inputs["From Max"].default_value = 1.0
                temp_links.new(combine_fac.outputs[0], map_range.inputs[0])

                power_node = make_node(temp_nodes, "ShaderNodeMath", "Hair Ombre Power", (100, 0))
                power_node.operation = 'POWER'
                power_node.use_clamp = True
                temp_links.new(map_range.outputs[0], power_node.inputs[0])
                power_node.inputs[1].default_value = power

                gain_node = make_node(temp_nodes, "ShaderNodeMath", "Hair Ombre Master Gain", (300, 0))
                gain_node.operation = 'MULTIPLY'
                gain_node.use_clamp = True
                temp_links.new(power_node.outputs[0], gain_node.inputs[0])
                gain_node.inputs[1].default_value = tip_str

                return gain_node.outputs[0]

        success = _bake_generated_mask(
            context,
            obj,
            target_img,
            target_node_name,
            ombre_bake_graph,
            colorspace=colorspace,
            prefill=prefill
        )

        if not success:
            for img in bpy.data.images:
                if img.name.startswith("TEMP_BAKE_COPY_"):
                    bpy.data.images.remove(img)
            self.report({'ERROR'}, "Hair Ombre bake failed. Ensure Cycles is available.")
            return {'CANCELLED'}

        for img in bpy.data.images:
            if img.name.startswith("TEMP_BAKE_COPY_"):
                bpy.data.images.remove(img)

        if self.bake_mode == "BASE_COLOR":
            mat.genos_base_color_map = target_img
            base_node = mat.node_tree.nodes.get("Base Color Map")
            if base_node:
                if "genos_orig_base" not in mat and base_node.image and base_node.image != target_img:
                    mat["genos_orig_base"] = base_node.image.name
                base_node.image = target_img
            
            # Disable dynamic ombre to prevent double-tinting the now-baked texture
            context.scene.genos_hair_tip_strength = 0.0
            
            self.report({'INFO'}, f"Hair Ombre baked into '{img_name}'. Dynamic ombre disabled to prevent double-tinting.")
        else:
            self.report({'INFO'}, f"Hair Ombre mask successfully baked into '{img_name}'!")

        return {'FINISHED'}


class GENOS_OT_apply_cloth_preset(bpy.types.Operator):
    bl_idname = "genos.apply_cloth_preset"
    bl_label = "Apply Cloth Shading Preset"
    bl_description = "Applies 2D anime presets for fabric, leather, or metallic clothing"

    mode: bpy.props.StringProperty(default="ANIME_MATTE")

    def execute(self, context):
        scene = context.scene
        m = self.mode
        scene.genos_cloth_spec_mode = m
        if m == "ANIME_MATTE":
            scene.genos_cloth_velvet_sheen = 0.45
            scene.genos_cloth_velvet_power = 2.8
            scene.genos_cloth_spec_str = 0.10
            scene.genos_cloth_shadow_bounce = (0.72, 0.74, 0.88, 1.0)
        elif m == "CRISP_CEL":
            scene.genos_cloth_velvet_sheen = 0.20
            scene.genos_cloth_velvet_power = 3.2
            scene.genos_cloth_spec_str = 0.65
            scene.genos_cloth_shadow_bounce = (0.65, 0.65, 0.75, 1.0)
        elif m == "METALLIC":
            scene.genos_cloth_velvet_sheen = 0.0
            scene.genos_cloth_spec_str = 1.0
            scene.genos_spec_metallic = True
        elif m == "HYBRID_AUTO":
            scene.genos_cloth_velvet_sheen = 0.40
            scene.genos_cloth_velvet_power = 2.8
            scene.genos_cloth_spec_str = 0.45
            scene.genos_cloth_shadow_bounce = (0.70, 0.72, 0.85, 1.0)

        obj = context.active_object
        count = 0
        if obj and obj.type == 'MESH':
            for slot in obj.material_slots:
                mat = slot.material
                if mat and mat.get("genos_shader_type") != 'HAIR' and mat.get("genos_shader_type") != 'FACE':
                    adapt_material_to_new_scheme(mat)
                    count += 1

        self.report({'INFO'}, f"Applied Clothing Preset '{m}' ({count} material(s) updated).")
        return {'FINISHED'}

class GENOS_OT_bake_specular(bpy.types.Operator):
    bl_idname = "genos.bake_specular"
    bl_label = "Auto-Bake Specular"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH': return {'CANCELLED'}

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

        mat_type = getattr(context.scene, "genos_spec_mat_type", "HAIR")

        if mat_type == 'HAIR':
            spec_socket = _hair_spec_graph(temp_mat.node_tree.nodes, temp_mat.node_tree.links, context.scene)
            temp_mat.node_tree.links.new(spec_socket, emit.inputs[0])
        elif mat_type == 'METAL':
            spec_socket = _metal_spec_graph(temp_mat.node_tree.nodes, temp_mat.node_tree.links, context.scene)
            temp_mat.node_tree.links.new(spec_socket, emit.inputs[0])
        elif mat_type == 'SKIN':
            spec_socket = _skin_spec_graph(temp_mat.node_tree.nodes, temp_mat.node_tree.links, context.scene)
            temp_mat.node_tree.links.new(spec_socket, emit.inputs[0])
        else:
            emit.inputs[0].default_value = (0.0, 0.0, 0.0, 1.0)

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
            
        base_name = material_base_name(mat)
        src_ao_name = f"{base_name}_Detail_AOSrc"
        ao_src_img = bpy.data.images.get(src_ao_name)
        if not is_valid_image(ao_src_img):
            ao_src_img = make_image(src_ao_name, scene_texture_size(), scene_texture_size(), alpha=True, colorspace=MASK_COLORSPACE, color=(1.0, 1.0, 1.0, 1.0))
        ao_img = ao_src_img
        configure_mask_image(ao_img)
        
        ao_node = mat.node_tree.nodes.get("Detail_AO")
        if ao_node:
            ao_node.image = ao_img

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
            try:
                ao_pixels = get_image_pixels(ao_img)
                if ao_pixels is not None and np is not None and isinstance(ao_pixels, np.ndarray):
                    # Clamp minimum AO to a soft shadow floor (0.25) so deep hair crevices don't become pitch black
                    r_chan = np.clip(ao_pixels[:, 0], 0.25, 1.0)
                    ao_pixels[:, 0] = r_chan
                    ao_pixels[:, 1] = r_chan
                    ao_pixels[:, 2] = r_chan
                    ao_pixels[:, 3] = 1.0
                    ao_img.pixels.foreach_set(ao_pixels.ravel())
                    ao_img.update()
            except Exception as e:
                print("AO Post-process warning:", e)
                
            pack_material_detail(mat)
            self.report({'INFO'}, "Successfully baked isolated AO and repacked Detail texture.")
            return {'FINISHED'}

        self.report({'ERROR'}, "AO bake failed. Check UVs and the active image target.")
        return {'CANCELLED'}
    
class GENOS_OT_bake_sdf(bpy.types.Operator):
    bl_idname = "genos.bake_sdf"
    bl_label = "Auto-Bake SDF Map"
    bl_description = "Bakes a baseline shadow threshold gradient based on the forward (-Y) normals"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH': return {'CANCELLED'}

        mat = obj.active_material
        if not mat: return {'CANCELLED'}

        sdf_node = mat.node_tree.nodes.get("SDF Map")
        if not sdf_node or not sdf_node.image:
            self.report({'ERROR'}, "No SDF Map found. Ensure this is a FACE shader.")
            return {'CANCELLED'}

        orig_mode = obj.mode
        if orig_mode != 'OBJECT':
            try: bpy.ops.object.mode_set(mode='OBJECT')
            except Exception: pass

        temp_mat = bpy.data.materials.new("TEMP_BAKE_SDF")
        temp_mat.use_nodes = True
        nodes = temp_mat.node_tree.nodes
        links = temp_mat.node_tree.links
        nodes.clear()

        out = nodes.new("ShaderNodeOutputMaterial")
        emit = nodes.new("ShaderNodeEmission")

        # Capture Mesh Normals
        geom = nodes.new("ShaderNodeNewGeometry")
        
        # Calculate Dot Product with Forward Axis (-Y)
        fwd_dot = nodes.new("ShaderNodeVectorMath")
        fwd_dot.operation = 'DOT_PRODUCT'
        fwd_dot.inputs[1].default_value = (0.0, -1.0, 0.0) 
        links.new(geom.outputs["Normal"], fwd_dot.inputs[0])

        # Map Normal range [-1, 1] to Color range [0, 1]
        map_range = nodes.new("ShaderNodeMapRange")
        map_range.inputs["From Min"].default_value = -1.0
        map_range.inputs["From Max"].default_value = 1.0
        links.new(fwd_dot.outputs["Value"], map_range.inputs["Value"])
        
        # Add a slight power curve for better facial shadow falloff
        power = nodes.new("ShaderNodeMath")
        power.operation = 'POWER'
        power.inputs[1].default_value = 1.2
        links.new(map_range.outputs["Result"], power.inputs[0])

        links.new(power.outputs["Value"], emit.inputs[0])
        links.new(emit.outputs[0], out.inputs[0])

        # Setup Target Image Node
        img_node = nodes.new("ShaderNodeTexImage")
        img_node.name = "SDF Map"
        img_node.label = "SDF Map"
        img_node.image = sdf_node.image
        nodes.active = img_node
        img_node.select = True

        orig_mats = [s.material for s in obj.material_slots]
        orig_active_index = obj.active_material_index
        success = False
        try:
            for s in obj.material_slots:
                s.material = temp_mat
            
            # Execute the internal bake pipeline (Emission mode)
            success = execute_bake(context, temp_mat, "SDF Map", is_ao=False)
        finally:
            # Restore original materials
            for i, s in enumerate(obj.material_slots):
                if i < len(orig_mats):
                    s.material = orig_mats[i]
            obj.active_material_index = orig_active_index
            bpy.data.materials.remove(temp_mat)
            if orig_mode != 'OBJECT':
                try: bpy.ops.object.mode_set(mode=orig_mode)
                except Exception: pass

        if success:
            self.report({'INFO'}, "Successfully baked baseline SDF Map.")
            return {'FINISHED'}

        self.report({'ERROR'}, "SDF bake failed. Check UVs and Active Object.")
        return {'CANCELLED'}

class GENOS_OT_bake_normal(bpy.types.Operator):
    bl_idname = "genos.bake_normal"
    bl_label = "Auto-Bake Normal Map"
    bl_description = "Bake the final tangent-space normal (normal map + bump/height detail) into a separate target, avoiding read/write feedback"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        mat = obj.active_material
        if not mat or not mat.use_nodes:
            return {'CANCELLED'}

        base = material_base_name(mat)
        normal_img = getattr(mat, 'genos_normal_map', None)
        if not is_valid_image(normal_img):
            normal_img = make_image(f"{base}_Normal", scene_texture_size(), scene_texture_size(), alpha=False, colorspace=MASK_COLORSPACE, color=(0.5, 0.5, 1.0, 1.0))
            try: mat.genos_normal_map = normal_img
            except Exception: pass
        configure_standard_map_image(normal_img, 'NORMAL')

        temp_img = _temporary_bake_target(base, 'NormalBake', normal=True, reference_img=normal_img)
        try:
            success = _bake_temp_material_pass(
                context, obj, mat, temp_img,
                pass_type='NORMAL',
                prefill=(0.5, 0.5, 1.0, 1.0)
            )
            if success and _copy_image_pixels(temp_img, normal_img):
                configure_standard_map_image(normal_img, 'NORMAL')
                try: normal_img.pack()
                except Exception: pass
                # Blender NORMAL bake outputs OpenGL (+Y) tangent-space normals.
                if getattr(context.scene, 'genos_normal_convention', 'OPENGL') != 'OPENGL':
                    try: context.scene.genos_normal_convention = 'OPENGL'
                    except Exception: pass
                self.report({'INFO'}, "Successfully baked final tangent-space Normal Map (OpenGL +Y).")
                return {'FINISHED'}
        finally:
            try: bpy.data.images.remove(temp_img)
            except Exception: pass

        self.report({'ERROR'}, "Normal bake failed. Check UVs, active mesh, and Cycles bake support.")
        return {'CANCELLED'}


class GENOS_OT_bake_displacement(bpy.types.Operator):
    bl_idname = "genos.bake_displacement"
    bl_label = "Auto-Bake Displacement Map"
    bl_description = "Bake the current height signal feeding the bump/displacement chain into a non-color height map using a separate target image"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        mat = obj.active_material
        if not mat or not mat.use_nodes:
            return {'CANCELLED'}

        base = material_base_name(mat)
        disp_img = getattr(mat, 'genos_displacement_map', None)
        if not is_valid_image(disp_img):
            disp_img = make_image(f"{base}_Displacement", scene_texture_size(), scene_texture_size(), alpha=False, colorspace=MASK_COLORSPACE, color=(0.5, 0.5, 0.5, 1.0))
            try: mat.genos_displacement_map = disp_img
            except Exception: pass
        configure_standard_map_image(disp_img, 'DISPLACEMENT')

        temp_img = _temporary_bake_target(base, 'DisplacementBake', normal=False, reference_img=disp_img)
        try:
            success = _bake_temp_material_pass(
                context, obj, mat, temp_img,
                pass_type='EMIT',
                source_input_node_name='Displacement Bump',
                source_input='Height',
                prefill=(float(getattr(context.scene, 'genos_displacement_midlevel', 0.5)),) * 3 + (1.0,)
            )
            if success and _copy_image_pixels(temp_img, disp_img):
                configure_standard_map_image(disp_img, 'DISPLACEMENT')
                try: disp_img.pack()
                except Exception: pass
                self.report({'INFO'}, "Successfully baked the current Displacement / Height signal.")
                return {'FINISHED'}
        finally:
            try: bpy.data.images.remove(temp_img)
            except Exception: pass

        self.report({'ERROR'}, "Displacement bake failed. Ensure the Displacement Bump Height input has a source and the mesh has UVs.")
        return {'CANCELLED'}


class GENOS_OT_bake_standard_data_maps(bpy.types.Operator):
    bl_idname = "genos.bake_standard_data_maps"
    bl_label = "Bake Standard Data Maps"
    bl_description = "Flatten assigned Roughness, Metallic, Opacity and AO map signals through the current shader into clean non-color textures"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or not obj.active_material:
            return {'CANCELLED'}
        mat = obj.active_material
        base = material_base_name(mat)
        specs = (
            ('genos_roughness_map', 'Roughness Map', 'Roughness', 'ROUGHNESS', (0.5,0.5,0.5,1.0)),
            ('genos_metallic_map', 'Metallic Map', 'Metallic', 'METALLIC', (0.0,0.0,0.0,1.0)),
            ('genos_opacity_map', 'Opacity Map', 'Opacity', 'OPACITY', (1.0,1.0,1.0,1.0)),
            ('genos_ao_map', 'AO Map', 'AO', 'AO', (1.0,1.0,1.0,1.0)),
        )
        baked = 0
        for prop, node_name, suffix, kind, default in specs:
            img = getattr(mat, prop, None)
            node = mat.node_tree.nodes.get(node_name) if mat.use_nodes else None
            if not is_valid_image(img) and node is None:
                continue
            if not is_valid_image(img):
                img = make_image(f'{base}_{suffix}', scene_texture_size(), scene_texture_size(), alpha=False, colorspace=MASK_COLORSPACE, color=default)
                try: setattr(mat, prop, img)
                except Exception: pass
            temp_img = _temporary_bake_target(base, suffix + 'Bake', reference_img=img)
            try:
                ok = _bake_temp_material_pass(context, obj, mat, temp_img, pass_type='EMIT', source_node_name=node_name, source_output='Color', prefill=default)
                if ok and _copy_image_pixels(temp_img, img):
                    configure_standard_map_image(img, kind)
                    try: img.pack()
                    except Exception: pass
                    baked += 1
            finally:
                try: bpy.data.images.remove(temp_img)
                except Exception: pass

        if baked:
            self.report({'INFO'}, f"Baked {baked} standard data map(s).")
            return {'FINISHED'}
        self.report({'WARNING'}, "No assigned Roughness / Metallic / Opacity / AO maps were available to bake.")
        return {'CANCELLED'}


class GENOS_OT_bake_curvature(bpy.types.Operator):
    bl_idname = "genos.bake_curvature"
    bl_label = "Auto-Bake Lineart"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH': return {'CANCELLED'}
        # Preview appearance uses Eevee-style compositing and does not require a UV bake.

        scene = context.scene
        edge_min = float(getattr(scene, "genos_lineart_edge_min", 0.01))
        edge_max = float(getattr(scene, "genos_lineart_edge_max", 0.15))
        if edge_max <= edge_min:
            edge_max = edge_min + 0.0001
        line_gamma = float(getattr(scene, "genos_lineart_gamma", 1.0))
        line_smooth = bool(getattr(scene, "genos_lineart_smooth", True))
        line_samples = int(getattr(scene, "genos_lineart_samples", 12))

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
        bevel.samples = line_samples

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

        if line_smooth:
            edge_map = temp_mat.node_tree.nodes.new("ShaderNodeMapRange")
            edge_map.interpolation_type = 'SMOOTHSTEP'
            edge_map.inputs["From Min"].default_value = edge_min
            edge_map.inputs["From Max"].default_value = edge_max
            edge_map.inputs["To Min"].default_value = 0.0
            edge_map.inputs["To Max"].default_value = 1.0
            temp_mat.node_tree.links.new(dist.outputs["Value"], edge_map.inputs["Value"])

            gamma = temp_mat.node_tree.nodes.new("ShaderNodeMath")
            gamma.operation = 'POWER'
            gamma.inputs[1].default_value = line_gamma
            temp_mat.node_tree.links.new(edge_map.outputs["Result"], gamma.inputs[0])
            temp_mat.node_tree.links.new(gamma.outputs["Value"], emit.inputs[0])
        else:
            ramp = temp_mat.node_tree.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = edge_min
            ramp.color_ramp.elements[0].color = (0,0,0,1)
            ramp.color_ramp.elements[1].position = edge_max
            ramp.color_ramp.elements[1].color = (1,1,1,1)

            temp_mat.node_tree.links.new(dist.outputs["Value"], ramp.inputs[0])

            gamma = temp_mat.node_tree.nodes.new("ShaderNodeMath")
            gamma.operation = 'POWER'
            gamma.inputs[1].default_value = line_gamma
            temp_mat.node_tree.links.new(ramp.outputs[0], gamma.inputs[0])
            temp_mat.node_tree.links.new(gamma.outputs["Value"], emit.inputs[0])
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

def _connect_mask_to_emission(nodes, links, emit_node, mask_socket):
    if mask_socket is None:
        return
    if getattr(mask_socket, "type", "") in {'VALUE', 'INT'}:
        comb = nodes.new("ShaderNodeCombineColor")
        comb.location = (300, 0)
        links.new(mask_socket, comb.inputs[0])
        links.new(mask_socket, comb.inputs[1])
        links.new(mask_socket, comb.inputs[2])
        links.new(comb.outputs[0], emit_node.inputs[0])
    else:
        links.new(mask_socket, emit_node.inputs[0])

def _bake_material_via_live_camera(context, src_obj, temp_mat, target_img):
    import os
    import tempfile
    size = target_img.size[0]
    proxy_obj, proxy_mesh = _make_uv_proxy_object(context, src_obj)
    if not proxy_obj: return False

    scene = context.scene
    for attr in src_obj.data.attributes:
        if attr.data_type in {'FLOAT_COLOR', 'BYTE_COLOR'}:
            proxy_attr = proxy_mesh.attributes.new(name=attr.name, type=attr.data_type, domain='POINT')
            proxy_v_idx = 0
            for poly in src_obj.data.polygons:
                for li in poly.loop_indices:
                    src_v_idx = src_obj.data.loops[li].vertex_index
                    if attr.domain == 'POINT': proxy_attr.data[proxy_v_idx].color = attr.data[src_v_idx].color
                    elif attr.domain == 'CORNER': proxy_attr.data[proxy_v_idx].color = attr.data[li].color
                    proxy_v_idx += 1

    bake_col_name = "GENOS_LIVE_BAKE_DATA"
    if bake_col_name in bpy.data.collections: bake_col = bpy.data.collections[bake_col_name]
    else:
        bake_col = bpy.data.collections.new(bake_col_name)
        scene.collection.children.link(bake_col)
        
    for ob in list(bake_col.objects): bake_col.objects.unlink(ob)

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

    orig_res_x, orig_res_y, orig_res_pct = scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage
    orig_film_transp, orig_color_mode = scene.render.film_transparent, scene.render.image_settings.color_mode
    orig_view_transform, orig_look = scene.view_settings.view_transform, scene.view_settings.look
    orig_filepath = scene.render.filepath
    
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100 
    
    tmp_dir = tempfile.mkdtemp(prefix='genos_eevee_')
    tmp_path = os.path.join(tmp_dir, 'bake_output.png')
    scene.render.filepath = tmp_path
    
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.view_settings.view_transform = 'Raw'
    scene.view_settings.look = 'None'

    orig_display = context.preferences.view.render_display_type
    context.preferences.view.render_display_type = 'WINDOW'
    context.view_layer.update() 
    try: bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    except: pass

    success = True
    try:
        bpy.ops.render.render('EXEC_DEFAULT', write_still=True)
        if os.path.exists(tmp_path):
            rendered_img = bpy.data.images.load(tmp_path)
            target_img.pixels = rendered_img.pixels
            bpy.data.images.remove(rendered_img)
            target_img.update()
        else: success = False
    except Exception as e:
        print('CAMERA_RENDER_ERROR:', e)
        success = False

    context.preferences.view.render_display_type = orig_display
    scene.camera = orig_camera
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = orig_res_x, orig_res_y, orig_res_pct
    scene.render.film_transparent, scene.render.image_settings.color_mode = orig_film_transp, orig_color_mode
    scene.view_settings.view_transform, scene.view_settings.look = orig_view_transform, orig_look
    scene.render.filepath = orig_filepath

    for ob, state in hidden_states.items(): ob.hide_render = state

    bpy.data.objects.remove(proxy_obj)
    bpy.data.meshes.remove(proxy_mesh)
    bpy.data.objects.remove(cam_obj)
    bpy.data.cameras.remove(cam_data)

    return success

def _bake_generated_mask(context, obj, target_img, target_node_name, graph_builder, *, colorspace=MASK_COLORSPACE, prefill=(0.0, 0.0, 0.0, 1.0)):
    if target_img is None:
        return False

    temp_mat = bpy.data.materials.new(f"TEMP_BAKE_{target_node_name}")
    temp_mat.use_nodes = True
    nodes = temp_mat.node_tree.nodes
    links = temp_mat.node_tree.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    emit = nodes.new("ShaderNodeEmission")
    links.new(emit.outputs[0], out.inputs[0])

    try:
        mask_socket = graph_builder(nodes, links, context.scene)
    except Exception:
        mask_socket = None
    _connect_mask_to_emission(nodes, links, emit, mask_socket)

    img_node = nodes.new("ShaderNodeTexImage")
    img_node.name = target_node_name
    img_node.label = target_node_name
    img_node.image = target_img
    nodes.active = img_node
    img_node.select = True

    orig_mode = obj.mode
    if orig_mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

    orig_mats = [slot.material for slot in obj.material_slots]
    orig_active_index = obj.active_material_index
    if not obj.material_slots:
        bpy.ops.object.material_slot_add()
        orig_mats = [None]

    for slot in obj.material_slots:
        slot.material = temp_mat

    success = False
    try:
        success = _bake_material_via_live_camera(context, obj, temp_mat, target_img)
        if success:
            try: target_img.pack()
            except Exception: pass
    finally:
        for i, slot in enumerate(obj.material_slots):
            if i < len(orig_mats):
                slot.material = orig_mats[i]
        obj.active_material_index = orig_active_index
        bpy.data.materials.remove(temp_mat)
        if orig_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode=orig_mode)
            except Exception:
                pass
    return success

def _build_dynamic_hair_band_bake_mask(nodes, links, scene, *, emission=False):
    """Bake the current dynamic hair-band stack into a stable texture-space mask.

    The bake deliberately omits the camera-facing gate because view-dependent LayerWeight
    is not stable during UV baking. Everything authored per band (coordinate mode, curve,
    shape, density, gap, softness, blur, opacity, strength, strand detail and emission)
    is preserved. The realtime shader can still add camera-facing behavior after export.
    """
    specs = [s for s in _hair_band_specs(scene) if s.get('enabled', True)]
    if emission:
        specs = [s for s in specs if s.get('emission_enabled', True) and s.get('emission_strength', 0.0) > 0.0]
    if not specs:
        zero = nodes.new('ShaderNodeValue'); zero.outputs[0].default_value = 0.0
        return zero.outputs[0]

    tex = nodes.new('ShaderNodeTexCoord'); tex.location = (-1500, 0)
    geo = nodes.new('ShaderNodeNewGeometry'); geo.location = (-1500, -180)
    gen_sep = nodes.new('ShaderNodeSeparateXYZ'); gen_sep.location = (-1300, 120); links.new(tex.outputs['Generated'], gen_sep.inputs[0])
    uv_sep = nodes.new('ShaderNodeSeparateXYZ'); uv_sep.location = (-1300, 20); links.new(tex.outputs['UV'], uv_sep.inputs[0])
    obj_sep = nodes.new('ShaderNodeSeparateXYZ'); obj_sep.location = (-1300, -80); links.new(tex.outputs['Object'], obj_sep.inputs[0])
    norm_sep = nodes.new('ShaderNodeSeparateXYZ'); norm_sep.location = (-1300, -180); links.new(geo.outputs['Normal'], norm_sep.inputs[0])
    axis_x = gen_sep.outputs['X']

    # One strand source for every baked band, matching the realtime shader's shared source.
    wave = nodes.new('ShaderNodeTexWave'); wave.location = (-1300, -360)
    wave.wave_type = 'BANDS'; wave.bands_direction = 'X'; wave.inputs['Scale'].default_value = float(getattr(scene, 'genos_hair_strands_scale', 24.0)); wave.inputs['Distortion'].default_value = 1.2
    links.new(tex.outputs['UV'], wave.inputs['Vector'])
    strand_step = nodes.new('ShaderNodeMapRange'); strand_step.location = (-1110, -360)
    configure_smooth_step(strand_step, 0.25, 0.65); links.new(wave.outputs['Color'], strand_step.inputs[0])
    strand_mul = nodes.new('ShaderNodeMath'); strand_mul.location = (-920, -360); strand_mul.operation = 'MULTIPLY'; strand_mul.inputs[1].default_value = max(0.0, float(getattr(scene, 'genos_hair_strands_strength', 0.65))); links.new(strand_step.outputs[0], strand_mul.inputs[0])
    strand_bias = nodes.new('ShaderNodeMath'); strand_bias.location = (-740, -360); strand_bias.operation = 'ADD'; strand_bias.inputs[1].default_value = max(0.2, 1.0 - max(0.0, float(getattr(scene, 'genos_hair_strands_strength', 0.65))) * 0.7); links.new(strand_mul.outputs[0], strand_bias.inputs[0])

    def elev_socket(mode):
        if mode == 'UV_V': return uv_sep.outputs['Y']
        if mode == 'NORMAL_Z': return norm_sep.outputs['Z']
        if mode == 'OBJECT_Z': return obj_sep.outputs['Z']
        return gen_sep.outputs['Z']

    combined = None
    for idx, spec in enumerate(specs):
        y = 360 - idx * 420
        elev = elev_socket(spec.get('coord_mode', 'GENERATED_Z'))
        width = max(0.001, float(spec['width']))
        blur = float(spec['blur']); softness = float(spec['softness'])
        half_width = max(0.02, 0.50 - float(spec['spot_gap']) * 0.48)

        center = nodes.new('ShaderNodeMath'); center.location = (-720, y); center.operation = 'SUBTRACT'; links.new(axis_x, center.inputs[0]); center.inputs[1].default_value = float(spec['curve_center'])
        square = nodes.new('ShaderNodeMath'); square.location = (-540, y); square.operation = 'MULTIPLY'; links.new(center.outputs[0], square.inputs[0]); links.new(center.outputs[0], square.inputs[1])
        curved = nodes.new('ShaderNodeMath'); curved.location = (-360, y); curved.operation = 'MULTIPLY_ADD'; links.new(square.outputs[0], curved.inputs[0]); curved.inputs[1].default_value = float(spec['curvature']) * 4.0; links.new(elev, curved.inputs[2])
        pos = nodes.new('ShaderNodeMath'); pos.location = (-180, y); pos.operation = 'SUBTRACT'; links.new(curved.outputs[0], pos.inputs[0]); pos.inputs[1].default_value = float(spec['position'])
        dist = nodes.new('ShaderNodeMath'); dist.location = (0, y); dist.operation = 'ABSOLUTE'; links.new(pos.outputs[0], dist.inputs[0])

        freq = nodes.new('ShaderNodeMath'); freq.location = (-540, y - 130); freq.operation = 'MULTIPLY'; links.new(axis_x, freq.inputs[0]); freq.inputs[1].default_value = float(spec['spot_density'])
        frac = nodes.new('ShaderNodeMath'); frac.location = (-360, y - 130); frac.operation = 'FRACT'; links.new(freq.outputs[0], frac.inputs[0])
        cell = nodes.new('ShaderNodeMath'); cell.location = (-180, y - 130); cell.operation = 'SUBTRACT'; links.new(frac.outputs[0], cell.inputs[0]); cell.inputs[1].default_value = 0.5
        xdist = nodes.new('ShaderNodeMath'); xdist.location = (0, y - 130); xdist.operation = 'ABSOLUTE'; links.new(cell.outputs[0], xdist.inputs[0])

        if spec['spot_shape'] == 'RIBBON':
            edge = nodes.new('ShaderNodeMapRange'); edge.location = (180, y)
            inner = max(0.0, width * (1.0 - softness - blur * 0.85)); outer = width * (1.0 + blur * 1.35)
            configure_smooth_step(edge, inner, outer, 1.0, 0.0); links.new(dist.outputs[0], edge.inputs[0])
            rib = nodes.new('ShaderNodeMapRange'); rib.location = (180, y - 130)
            rf = 0.015 + blur * 0.18
            configure_smooth_step(rib, max(0.0, half_width-rf), half_width+rf, 1.0, min(0.55, blur*0.35)); links.new(xdist.outputs[0], rib.inputs[0])
            shape = nodes.new('ShaderNodeMath'); shape.location = (380, y-50); shape.operation = 'MULTIPLY'; links.new(edge.outputs[0], shape.inputs[0]); links.new(rib.outputs[0], shape.inputs[1])
            shape_socket = shape.outputs[0]
        else:
            aspect = 1.0 if spec['spot_shape'] == 'ROUND' else float(spec['spot_aspect'])
            xnorm = nodes.new('ShaderNodeMath'); xnorm.location = (180, y); xnorm.operation = 'DIVIDE'; links.new(xdist.outputs[0], xnorm.inputs[0]); xnorm.inputs[1].default_value = max(0.035, half_width * aspect)
            ynorm = nodes.new('ShaderNodeMath'); ynorm.location = (180, y-100); ynorm.operation = 'DIVIDE'; links.new(dist.outputs[0], ynorm.inputs[0]); ynorm.inputs[1].default_value = width
            vec = nodes.new('ShaderNodeCombineXYZ'); vec.location = (380, y-50); links.new(xnorm.outputs[0], vec.inputs['X']); links.new(ynorm.outputs[0], vec.inputs['Y'])
            length = nodes.new('ShaderNodeVectorMath'); length.location = (560, y-50); length.operation = 'LENGTH'; links.new(vec.outputs[0], length.inputs[0])
            circ = nodes.new('ShaderNodeMapRange'); circ.location = (740, y-50)
            cf = 0.025 + softness*0.20 + blur*0.40
            configure_smooth_step(circ, max(0.0,1.0-cf), 1.0+cf, 1.0, 0.0); links.new(length.outputs['Value'], circ.inputs[0])
            shape_socket = circ.outputs[0]

        strand_mix = nodes.new('ShaderNodeMix'); strand_mix.location = (920, y-50); strand_mix.data_type = 'FLOAT'
        find_socket(strand_mix.inputs, 'Factor').default_value = float(spec['strand_detail']) * (1.0 - blur*0.85)
        find_socket(strand_mix.inputs, 'A').default_value = 1.0; links.new(strand_bias.outputs[0], find_socket(strand_mix.inputs, 'B'))
        detailed = nodes.new('ShaderNodeMath'); detailed.location = (1100, y-50); detailed.operation = 'MULTIPLY'; links.new(shape_socket, detailed.inputs[0]); links.new(find_socket(strand_mix.outputs, 'Result'), detailed.inputs[1])

        gain_value = max(0.0, float(spec['opacity']) * float(spec['strength']))
        if emission:
            gain_value *= min(1.0, max(0.0, float(spec['emission_strength'])))
        gain = nodes.new('ShaderNodeMath'); gain.location = (1280, y-50); gain.operation = 'MULTIPLY'; links.new(detailed.outputs[0], gain.inputs[0]); gain.inputs[1].default_value = gain_value

        if combined is None:
            combined = gain.outputs[0]
        else:
            union = nodes.new('ShaderNodeMath'); union.location = (1460, y-50); union.operation = 'MAXIMUM'; links.new(combined, union.inputs[0]); links.new(gain.outputs[0], union.inputs[1]); combined = union.outputs[0]

    clamp = nodes.new('ShaderNodeClamp'); clamp.location = (1660, 0); links.new(combined, clamp.inputs['Value'])
    clamp.inputs['Min'].default_value = 0.0; clamp.inputs['Max'].default_value = 1.0
    return clamp.outputs['Result']


def _hair_spec_graph(nodes, links, scene):
    return _build_dynamic_hair_band_bake_mask(nodes, links, scene, emission=False)


def _hair_emission_graph(nodes, links, scene):
    return _build_dynamic_hair_band_bake_mask(nodes, links, scene, emission=True)

def _hair_rim_graph(nodes, links, scene):
    facing = nodes.new("ShaderNodeLayerWeight")
    facing.location = (-600, 0)
    facing.inputs["Blend"].default_value = 0.15

    rim_power = max(0.5, float(getattr(scene, "genos_rim_power", 3.0)))
    rim_intensity = max(0.0, float(getattr(scene, "genos_rim_intensity", 1.0)))

    power = nodes.new("ShaderNodeMath")
    power.location = (-380, 0)
    power.operation = 'POWER'
    power.inputs[1].default_value = rim_power
    links.new(facing.outputs["Facing"], power.inputs[0])

    gain = nodes.new("ShaderNodeMath")
    gain.location = (-160, 0)
    gain.operation = 'MULTIPLY'
    gain.inputs[1].default_value = rim_intensity * max(0.0, float(getattr(scene, "genos_hair_highlight_strength", 1.0)))
    links.new(power.outputs[0], gain.inputs[0])

    clamp = nodes.new("ShaderNodeClamp")
    clamp.location = (60, 0)
    links.new(gain.outputs[0], clamp.inputs["Value"])
    return clamp.outputs["Result"]

def _hair_accent_graph(nodes, links, scene):
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (-1100, 0)

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-900, 0)
    links.new(uv.outputs["UV"], sep.inputs[0])

    # Root to tip gradient along V (1.0 - Y)
    inv_y = nodes.new("ShaderNodeMath")
    inv_y.location = (-700, 100)
    inv_y.operation = 'SUBTRACT'
    inv_y.inputs[0].default_value = 1.0
    links.new(sep.outputs["Y"], inv_y.inputs[1])

    # Organic strand variation
    wave = nodes.new("ShaderNodeTexWave")
    wave.location = (-700, -150)
    wave.wave_type = 'BANDS'
    wave.bands_direction = 'X'
    wave.inputs["Scale"].default_value = 24.0
    wave.inputs["Distortion"].default_value = 1.5
    links.new(uv.outputs["UV"], wave.inputs["Vector"])

    mix_tip = nodes.new("ShaderNodeMath")
    mix_tip.location = (-480, 0)
    mix_tip.operation = 'MULTIPLY'
    links.new(inv_y.outputs[0], mix_tip.inputs[0])
    links.new(wave.outputs["Color"], mix_tip.inputs[1])

    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-260, 0)
    ramp.color_ramp.elements[0].position = 0.25
    ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    ramp.color_ramp.elements[1].position = 0.85
    ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    links.new(mix_tip.outputs[0], ramp.inputs["Fac"])

    gain = nodes.new("ShaderNodeMath")
    gain.location = (-40, 0)
    gain.operation = 'MULTIPLY'
    gain.inputs[1].default_value = max(0.0, float(getattr(scene, "genos_hair_tip_strength", 0.6)))
    links.new(ramp.outputs["Color"], gain.inputs[0])

    clamp = nodes.new("ShaderNodeClamp")
    clamp.location = (160, 0)
    links.new(gain.outputs[0], clamp.inputs["Value"])
    return clamp.outputs["Result"]

def _metal_spec_graph(nodes, links, scene):
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (-900, 0)

    noise = nodes.new("ShaderNodeTexNoise")
    noise.location = (-650, 0)
    noise.inputs["Scale"].default_value = 25.0
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.4
    links.new(uv.outputs["UV"], noise.inputs["Vector"])

    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-420, 0)
    ramp.color_ramp.elements[0].position = 0.50
    ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    ramp.color_ramp.elements[1].position = 0.68
    ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])

    facing = nodes.new("ShaderNodeLayerWeight")
    facing.location = (-420, -180)
    facing.inputs["Blend"].default_value = 0.35

    comb = nodes.new("ShaderNodeMath")
    comb.location = (-200, 0)
    comb.operation = 'MAXIMUM'
    links.new(ramp.outputs["Color"], comb.inputs[0])
    links.new(facing.outputs["Facing"], comb.inputs[1])

    strength = nodes.new("ShaderNodeMath")
    strength.location = (20, 0)
    strength.operation = 'MULTIPLY'
    strength.inputs[1].default_value = max(0.0, float(getattr(scene, "genos_spec_core_strength", 1.2)))
    links.new(comb.outputs[0], strength.inputs[0])

    clamp = nodes.new("ShaderNodeClamp")
    clamp.location = (240, 0)
    links.new(strength.outputs[0], clamp.inputs["Value"])
    return clamp.outputs["Result"]

def _skin_spec_graph(nodes, links, scene):
    fresnel = nodes.new("ShaderNodeFresnel")
    fresnel.location = (-500, 0)
    fresnel.inputs["IOR"].default_value = 1.05

    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.location = (-280, 0)
    ramp.color_ramp.elements[0].position = 0.05
    ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    ramp.color_ramp.elements[1].position = 0.50
    ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    links.new(fresnel.outputs[0], ramp.inputs["Fac"])

    gain = nodes.new("ShaderNodeMath")
    gain.location = (-50, 0)
    gain.operation = 'MULTIPLY'
    gain.inputs[1].default_value = 0.25 * max(0.0, float(getattr(scene, "genos_spec_halo_strength", 0.6)))
    links.new(ramp.outputs["Color"], gain.inputs[0])
    return gain.outputs[0]

def _eye_sparkle_graph(nodes, links, scene):
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (-900, 0)

    voro = nodes.new("ShaderNodeTexVoronoi")
    voro.location = (-650, 0)
    voro.feature = 'F1'
    voro.inputs["Scale"].default_value = 95.0
    voro.inputs["Randomness"].default_value = 0.95
    links.new(uv.outputs["UV"], voro.inputs["Vector"])

    mapr = nodes.new("ShaderNodeMapRange")
    mapr.location = (-420, 0)
    mapr.interpolation_type = 'SMOOTHSTEP'
    mapr.inputs["From Min"].default_value = 0.0
    mapr.inputs["From Max"].default_value = 0.035
    mapr.inputs["To Min"].default_value = 1.0
    mapr.inputs["To Max"].default_value = 0.0
    links.new(voro.outputs["Distance"], mapr.inputs["Value"])

    gain = nodes.new("ShaderNodeMath")
    gain.location = (-180, 0)
    gain.operation = 'MULTIPLY'
    gain.inputs[1].default_value = max(0.0, getattr(scene, "genos_eye_sparkle_strength", 1.0))
    links.new(mapr.outputs["Result"], gain.inputs[0])
    return gain.outputs[0]

def _eye_ring_graph(nodes, links, scene):
    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (-1200, 100)

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-1000, 100)
    links.new(uv.outputs["UV"], sep.inputs[0])

    off_x = nodes.new("ShaderNodeMath")
    off_x.location = (-820, 180)
    off_x.operation = 'SUBTRACT'
    links.new(sep.outputs["X"], off_x.inputs[0])
    off_x.inputs[1].default_value = 0.5

    off_y = nodes.new("ShaderNodeMath")
    off_y.location = (-820, 20)
    off_y.operation = 'SUBTRACT'
    links.new(sep.outputs["Y"], off_y.inputs[0])
    off_y.inputs[1].default_value = 0.5

    vec = nodes.new("ShaderNodeCombineXYZ")
    vec.location = (-620, 100)
    links.new(off_x.outputs[0], vec.inputs["X"])
    links.new(off_y.outputs[0], vec.inputs["Y"])

    length = nodes.new("ShaderNodeVectorMath")
    length.location = (-420, 100)
    length.operation = 'LENGTH'
    links.new(vec.outputs[0], length.inputs[0])

    outer = nodes.new("ShaderNodeMapRange")
    outer.location = (-200, 180)
    outer.interpolation_type = 'SMOOTHSTEP'
    outer.inputs["From Min"].default_value = 0.18
    outer.inputs["From Max"].default_value = 0.39
    outer.inputs["To Min"].default_value = 1.0
    outer.inputs["To Max"].default_value = 0.0
    links.new(length.outputs["Value"], outer.inputs["Value"])

    inner = nodes.new("ShaderNodeMapRange")
    inner.location = (-200, 20)
    inner.interpolation_type = 'SMOOTHSTEP'
    inner.inputs["From Min"].default_value = 0.10
    inner.inputs["From Max"].default_value = 0.22
    inner.inputs["To Min"].default_value = 0.0
    inner.inputs["To Max"].default_value = 1.0
    links.new(length.outputs["Value"], inner.inputs["Value"])

    ring = nodes.new("ShaderNodeMath")
    ring.location = (20, 100)
    ring.operation = 'MULTIPLY'
    links.new(outer.outputs["Result"], ring.inputs[0])
    links.new(inner.outputs["Result"], ring.inputs[1])

    gain = nodes.new("ShaderNodeMath")
    gain.location = (240, 100)
    gain.operation = 'MULTIPLY'
    gain.inputs[1].default_value = max(0.0, getattr(scene, "genos_eye_sparkle_strength", 1.0)) * 0.75
    links.new(ring.outputs[0], gain.inputs[0])
    return gain.outputs[0]

def _eye_spec_graph(nodes, links, scene):
    sparkle = _eye_sparkle_graph(nodes, links, scene)
    ring = _eye_ring_graph(nodes, links, scene)
    blend = nodes.new("ShaderNodeMath")
    blend.location = (520, 40)
    blend.operation = 'MAXIMUM'
    links.new(sparkle, blend.inputs[0])
    links.new(ring, blend.inputs[1])
    return blend.outputs[0]

def _eye_emission_detail_graph(nodes, links, scene):
    sparkle = _eye_sparkle_graph(nodes, links, scene)
    soft = nodes.new("ShaderNodeMath")
    soft.location = (80, 0)
    soft.operation = 'MULTIPLY'
    soft.inputs[1].default_value = 0.6
    links.new(sparkle, soft.inputs[0])
    return soft.outputs[0]

class GENOS_OT_bake_anime_fx(bpy.types.Operator):
    bl_idname = "genos.bake_anime_fx"
    bl_label = "Auto-Bake Anime Hair/Eye FX"
    bl_description = "Bake dynamic hair highlight/angel-ring spec + ILM.G glow masks, rim/accent FX, or anime eye FX into texture source maps"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}

        mat = obj.active_material
        if not mat or not mat.use_nodes or "is_anime_toon" not in mat:
            return {'CANCELLED'}

        shader_type = mat.get("genos_shader_type", "DEFAULT")
        if shader_type == 'HAIR':
            # Dynamic highlight masks now bake to ILM.B and their glow masks to ILM.G.
            bake_plan = [
                ("ILM_Spec", _hair_spec_graph, (0.0, 0.0, 0.0, 1.0), "ilm"),
                ("ILM_Emission", _hair_emission_graph, (0.0, 0.0, 0.0, 1.0), "ilm"),
                ("ILM_Rim", _hair_rim_graph, (0.0, 0.0, 0.0, 1.0), "ilm"),
                ("Detail_Accent", _hair_accent_graph, (0.0, 0.0, 0.0, 1.0), "detail"),
            ]
        elif shader_type == 'FACE' and any(k in mat.name.lower() for k in ('eye', 'iris', 'pupil')):
            bake_plan = [
                ("ILM_Spec", _eye_spec_graph, (0.0, 0.0, 0.0, 1.0), "ilm"),
                ("ILM_Emission", _eye_sparkle_graph, (0.0, 0.0, 0.0, 1.0), "ilm"),
                ("Detail_Accent", _eye_ring_graph, (0.0, 0.0, 0.0, 1.0), "detail"),
                ("Detail_Emission", _eye_emission_detail_graph, (0.0, 0.0, 0.0, 1.0), "detail"),
            ]
        else:
            self.report({'WARNING'}, "Anime FX auto-bake applies to HAIR or eye/iris FACE materials.")
            return {'CANCELLED'}

        baked = 0
        ilm_touched = False
        detail_touched = False

        suffix_map = {
            "ILM_Spec": "ILM_SpecSrc", "ILM_Emission": "ILM_EmissionSrc", "ILM_Rim": "ILM_RimSrc",
            "Detail_Accent": "Detail_AccentSrc", "Detail_Emission": "Detail_EmissionSrc",
        }
        for node_name, builder, prefill, pack_group in bake_plan:
            node = mat.node_tree.nodes.get(node_name)
            if node is None:
                node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                node.name = node_name; node.label = node_name
            target_img = node.image if hasattr(node, "image") else None
            if target_img is None:
                target_img = ensure_source_image(mat, node_name, suffix_map.get(node_name, node_name + "Src"), prefill, MASK_COLORSPACE)
                node.image = target_img
            set_image_colorspace(target_img, MASK_COLORSPACE)
            if _bake_generated_mask(context, obj, target_img, node_name, builder, colorspace=MASK_COLORSPACE, prefill=prefill):
                baked += 1
                if pack_group == "ilm":
                    ilm_touched = True
                elif pack_group == "detail":
                    detail_touched = True

        if baked == 0:
            self.report({'ERROR'}, "Anime FX bake failed or no target maps were found on this material.")
            return {'CANCELLED'}

        if ilm_touched:
            pack_material_ilm(mat)
        if detail_touched:
            pack_material_detail(mat)

        if shader_type == 'HAIR':
            self.report({'INFO'}, f"Baked {baked} dynamic hair FX channels (including highlight + ILM.G glow) and repacked maps.")
        else:
            self.report({'INFO'}, f"Baked {baked} anime eye FX channels and repacked maps.")
        return {'FINISHED'}

class GENOS_OT_download_pattern_preset(bpy.types.Operator):
    bl_idname = "genos.download_pattern_preset"
    bl_label = "Download Pattern Preset"
    bl_description = "Download selected pattern texture pack and cache it offline"

    pattern_key: EnumProperty(
        name="Pattern Key",
        items=[
            ("PANTYHOSE", "Pantyhose", ""),
            ("STRIPES", "Stripes", ""),
            ("RIPPED", "Ripped", ""),
            ("BODYSUIT_HEX", "Bodysuit Hex", ""),
            ("DOTS", "Dots", ""),
            ("COTTON", "Cotton", ""),
            ("LEATHER", "Leather", ""),
        ],
        default="PANTYHOSE"
    )

    def execute(self, context):
        try:
            result = download_pattern_preset(context.scene, self.pattern_key)
        except Exception as e:
            try:
                context.scene.genos_pattern_last_download_report = f"{self.pattern_key}: {e}"
            except Exception:
                pass
            self.report({'ERROR'}, f"Pattern download failed ({self.pattern_key}): {e}")
            return {'CANCELLED'}

        try:
            context.scene.genos_pattern_last_download_report = f"{self.pattern_key}: OK"
        except Exception:
            pass

        has_color = bool(result.get("color"))
        if has_color:
            self.report({'INFO'}, f"Downloaded {self.pattern_key} to offline cache.")
            return {'FINISHED'}
        self.report({'WARNING'}, f"Downloaded {self.pattern_key}, but no color map was found in the ZIP.")
        return {'FINISHED'}

class GENOS_OT_download_all_pattern_presets(bpy.types.Operator):
    bl_idname = "genos.download_all_pattern_presets"
    bl_label = "Download All Pattern Presets"
    bl_description = "Download all configured pattern preset texture packs for offline use"

    def execute(self, context):
        ok = 0
        fail = 0
        errors = []
        for key in PATTERN_PRESET_KEYS:
            try:
                result = download_pattern_preset(context.scene, key)
                if result.get("color"):
                    ok += 1
                else:
                    fail += 1
                    errors.append(f"{key}: no color map detected")
            except Exception as e:
                fail += 1
                errors.append(f"{key}: {e}")

        report_text = " | ".join(errors[:6]) if errors else "OK"
        try:
            context.scene.genos_pattern_last_download_report = report_text
        except Exception:
            pass
        if ok == 0:
            self.report({'ERROR'}, f"No pattern presets were downloaded successfully. {report_text}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Downloaded {ok} preset(s) to offline cache. Failed: {fail}. {report_text}")
        return {'FINISHED'}

class GENOS_OT_apply_cached_pattern_preset(bpy.types.Operator):
    bl_idname = "genos.apply_cached_pattern_preset"
    bl_label = "Apply Cached Pattern Preset"
    bl_description = "Link cached pattern textures to active material and rebuild node tree"

    pattern_key: EnumProperty(
        name="Pattern Key",
        items=[
            ("PANTYHOSE", "Pantyhose", ""),
            ("STRIPES", "Stripes", ""),
            ("RIPPED", "Ripped", ""),
            ("BODYSUIT_HEX", "Bodysuit Hex", ""),
            ("DOTS", "Dots", ""),
            ("COTTON", "Cotton", ""),
            ("LEATHER", "Leather", ""),
        ],
        default="PANTYHOSE"
    )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or not obj.active_material:
            return {'CANCELLED'}
        mat = obj.active_material
        if "is_anime_toon" not in mat:
            self.report({'ERROR'}, "Active material is not an AnimeToon shader.")
            return {'CANCELLED'}

        paths = cached_pattern_paths(context.scene, self.pattern_key)
        if not paths.get("color"):
            self.report({'ERROR'}, f"No cached color map found for {self.pattern_key}. Download first.")
            return {'CANCELLED'}

        color_img = load_or_reload_image(paths.get("color"), non_color=False)
        rough_img = load_or_reload_image(paths.get("roughness"), non_color=True) if paths.get("roughness") else None
        normal_img = load_or_reload_image(paths.get("normal"), non_color=True) if paths.get("normal") else None
        if color_img is None:
            self.report({'ERROR'}, f"Failed to load cached color map for {self.pattern_key}.")
            return {'CANCELLED'}

        try:
            mat.genos_pattern_color_map = color_img
            if rough_img:
                mat.genos_pattern_roughness_map = rough_img
            if normal_img:
                mat.genos_pattern_normal_map = normal_img
            old_tint = tuple(getattr(context.scene, "genos_pattern_tint", (1.0, 1.0, 1.0, 1.0)))
            if len(old_tint) >= 3 and abs(old_tint[0] - 0.08) < 1e-6 and abs(old_tint[1] - 0.08) < 1e-6 and abs(old_tint[2] - 0.08) < 1e-6:
                context.scene.genos_pattern_tint = (1.0, 1.0, 1.0, 1.0)
        except Exception:
            pass

        if self.pattern_key in {"PANTYHOSE", "STRIPES", "RIPPED", "BODYSUIT_HEX", "DOTS", "COTTON", "LEATHER"}:
            context.scene.genos_clothing_pattern_type = self.pattern_key

        try:
            bpy.ops.genos.regenerate_shader()
        except Exception as e:
            self.report({'WARNING'}, f"Pattern assigned, but shader regen failed: {e}")
            return {'FINISHED'}

        self.report({'INFO'}, f"Applied cached {self.pattern_key} pattern to {mat.name}.")
        return {'FINISHED'}

class GENOS_OT_set_paint_target(bpy.types.Operator):
    bl_idname = "genos.set_paint_target"
    bl_label = "Set Paint Target"

    def execute(self, context):
        obj = active_mesh_object(context)
        if not obj or not obj.active_material: return {'CANCELLED'}
        mat = obj.active_material
        
        target = context.scene.genos_paint_target
        node = set_active_image_node(mat, target)

        # Ensure Pattern Mask always has a valid image so painting affects shader output.
        if target == "PATTERN_MASK" and node is not None and getattr(node, "image", None) is None:
            try:
                img = ensure_source_image(mat, "Pattern Mask", "PatternMask", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE)
                node.image = img
                try:
                    mat["genos_pattern_mask_image"] = img.name
                except Exception:
                    pass
            except Exception as e:
                self.report({'ERROR'}, f"Failed to create Pattern Mask image: {e}")
                return {'CANCELLED'}
        
        if node is None or node.image is None: return {'CANCELLED'}
        set_image_colorspace(node.image, MASK_COLORSPACE if target == "PATTERN_MASK" else "sRGB")
        try:
            node.image.update()
        except Exception:
            pass
        
        # FIXED: Tell Blender's active tool system to target the node's image directly
        try:
            if getattr(context.tool_settings, "image_paint", None):
                context.tool_settings.image_paint.mode = 'IMAGE'
                context.tool_settings.image_paint.canvas = node.image
                # Pattern mask uses black=off, white=on; default brush to white reveal.
                if target == "PATTERN_MASK" and context.tool_settings.image_paint.brush:
                    brush = context.tool_settings.image_paint.brush
                    brush.color = (1.0, 1.0, 1.0)
                    try:
                        brush.secondary_color = (0.0, 0.0, 0.0)
                    except Exception:
                        pass
                    try:
                        brush.strength = 1.0
                    except Exception:
                        pass
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

        # Keep exported packed maps synchronized with procedural material FX.
        if getattr(s, 'genos_autobake_fx_on_export', True) and mat.get('is_anime_toon'):
            sh = mat.get('genos_shader_type', 'DEFAULT')
            low_name = mat.name.lower()
            if sh == 'HAIR' or (sh == 'FACE' and any(k in low_name for k in ('eye', 'iris', 'pupil'))):
                try:
                    bpy.ops.genos.bake_anime_fx()
                except Exception as exc:
                    print('[Anime Studio] Auto-bake FX on export skipped: %s' % exc)

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

        def create_basecolor_export_material(mat_name, base_img, pattern_mask_img=None, pattern_color_img=None):
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

            base_tex = nodes.new("ShaderNodeTexImage")
            base_tex.name = "ExportBaseColor"
            base_tex.image = base_img
            base_tex.interpolation = 'Linear'
            links.new(uv_node.outputs[0], base_tex.inputs["Vector"])

            final_color = base_tex.outputs["Color"]

            pattern_type = getattr(s, "genos_clothing_pattern_type", "NONE")
            pattern_strength_val = max(0.0, min(1.0, float(getattr(s, "genos_pattern_strength", 0.55))))
            use_pattern = pattern_type != "NONE" and pattern_mask_img is not None and (pattern_color_img is not None or pattern_strength_val > 0.0)

            if use_pattern:
                p_map = nodes.new("ShaderNodeMapping")
                p_map.name = "ExportPatternMapping"
                links.new(uv_node.outputs[0], p_map.inputs["Vector"])
                try:
                    sc = max(0.01, float(getattr(s, "genos_pattern_scale", 20.0)))
                    p_map.inputs["Scale"].default_value = (sc, sc, 1.0)
                    p_map.inputs["Rotation"].default_value = (0.0, 0.0, float(getattr(s, "genos_pattern_rotation", 0.0)))
                except Exception:
                    pass

                mask_tex = nodes.new("ShaderNodeTexImage")
                mask_tex.name = "ExportPatternMask"
                mask_tex.image = pattern_mask_img
                mask_tex.interpolation = 'Linear'
                links.new(uv_node.outputs[0], mask_tex.inputs["Vector"])

                mask_bw = nodes.new("ShaderNodeRGBToBW")
                links.new(mask_tex.outputs["Color"], mask_bw.inputs["Color"])

                mask_strength = nodes.new("ShaderNodeMath")
                mask_strength.operation = 'MULTIPLY'
                mask_strength.use_clamp = True
                mask_strength.inputs[1].default_value = pattern_strength_val
                links.new(mask_bw.outputs["Val"], mask_strength.inputs[0])

                if pattern_color_img is not None:
                    p_tex = nodes.new("ShaderNodeTexImage")
                    p_tex.name = "ExportPatternColor"
                    p_tex.image = pattern_color_img
                    p_tex.interpolation = 'Linear'
                    links.new(p_map.outputs["Vector"], p_tex.inputs["Vector"])

                    tint_mul = nodes.new("ShaderNodeMix")
                    tint_mul.data_type = 'RGBA'
                    tint_mul.blend_type = 'MULTIPLY'
                    find_socket(tint_mul.inputs, "Factor", "Fac").default_value = 1.0
                    links.new(p_tex.outputs["Color"], find_socket(tint_mul.inputs, "A", "Color1"))
                    find_socket(tint_mul.inputs, "B", "Color2").default_value = tuple(getattr(s, "genos_pattern_tint", (1.0, 1.0, 1.0, 1.0)))
                    pattern_color = find_socket(tint_mul.outputs, "Result", "Color")
                else:
                    rgb = nodes.new("ShaderNodeRGB")
                    rgb.outputs[0].default_value = tuple(getattr(s, "genos_pattern_tint", (1.0, 1.0, 1.0, 1.0)))
                    pattern_color = rgb.outputs[0]

                # Match live shader behavior: procedural pattern shapes the pattern detail,
                # while the painted mask controls where it is applied.
                pattern_proc = _build_clothing_pattern_factor(nodes, links, s, pattern_type)
                pattern_detail = nodes.new("ShaderNodeMix")
                pattern_detail.data_type = 'RGBA'
                pattern_detail.blend_type = 'MULTIPLY'
                find_socket(pattern_detail.inputs, "Factor", "Fac").default_value = 1.0
                links.new(pattern_color, find_socket(pattern_detail.inputs, "A", "Color1"))
                links.new(pattern_proc, find_socket(pattern_detail.inputs, "B", "Color2"))

                base_mul = nodes.new("ShaderNodeMix")
                base_mul.data_type = 'RGBA'
                base_mul.blend_type = 'MULTIPLY'
                find_socket(base_mul.inputs, "Factor", "Fac").default_value = 1.0
                links.new(base_tex.outputs["Color"], find_socket(base_mul.inputs, "A", "Color1"))
                links.new(find_socket(pattern_detail.outputs, "Result", "Color"), find_socket(base_mul.inputs, "B", "Color2"))

                pattern_mix = nodes.new("ShaderNodeMix")
                pattern_mix.data_type = 'RGBA'
                pattern_mix.blend_type = 'MIX'
                links.new(mask_strength.outputs[0], find_socket(pattern_mix.inputs, "Factor", "Fac"))
                links.new(base_tex.outputs["Color"], find_socket(pattern_mix.inputs, "A", "Color1"))
                links.new(find_socket(base_mul.outputs, "Result", "Color"), find_socket(pattern_mix.inputs, "B", "Color2"))
                final_color = find_socket(pattern_mix.outputs, "Result", "Color")

            links.new(final_color, emit.inputs["Color"])

            transp = nodes.new("ShaderNodeBsdfTransparent")
            mix = nodes.new("ShaderNodeMixShader")
            links.new(transp.outputs[0], mix.inputs[1])
            links.new(emit.outputs[0], mix.inputs[2])
            links.new(base_tex.outputs["Alpha"], mix.inputs["Fac"])
            links.new(mix.outputs[0], out.inputs[0])
            return temp_mat

        packs_to_render = []

        # Packed data maps are saved directly. Camera rendering them can apply view
        # transforms and, in the old path, dropped ILM.A and Detail.B/A entirely.
        packed_ilm = pack_material_ilm(mat)
        packed_detail = pack_material_detail(mat)
        if is_valid_image(packed_ilm):
            configure_mask_image(packed_ilm, packed=True)
            if save_image(packed_ilm, out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_ilm', '_ILM')}.png"):
                saved_count += 1
        if is_valid_image(packed_detail):
            configure_mask_image(packed_detail, packed=True)
            if save_image(packed_detail, out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_detail', '_Detail')}.png"):
                saved_count += 1

        # Standard color maps. BaseColor still camera-bakes the optional procedural
        # clothing pattern; pure data maps are exported directly below.
        base_img = get_img("BaseColor")
        if base_img:
            packs_to_render.append((
                create_basecolor_export_material(
                    "TEMP_PACK_BASE",
                    base_img,
                    get_img("Pattern Mask"),
                    getattr(mat, "genos_pattern_color_map", None)
                ),
                f"{mat_base}{getattr(s, 'genos_exp_suf_albedo', '_BaseColor')}.png",
                True
            ))
            
        emit_img = getattr(mat, 'genos_emission_map', None) or get_img("Emission Map")
        if is_valid_image(emit_img):
            configure_standard_map_image(emit_img, 'EMISSION')
            if save_image(emit_img, out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_emission', '_Emission')}.png"):
                saved_count += 1

        # Face SDF is a data texture and must bypass color management too.
        if mat.get("genos_shader_type") == 'FACE':
            sdf_img = getattr(mat, 'genos_sdf_map', None) or get_img("SDF Map")
            if is_valid_image(sdf_img):
                configure_standard_map_image(sdf_img, 'SDF')
                if save_image(sdf_img, out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_sdf', '_SDF')}.png"):
                    saved_count += 1

        # Data maps are exported directly from their pixel buffers instead of being
        # camera-rendered. This avoids view transforms/gamma from altering normals,
        # height, roughness, metallic, AO or opacity values.

        # Execute Live Camera Baking Queue
        for temp_mat, target_filename, use_alpha in packs_to_render:
            filepath = os.path.join(out_dir, target_filename)
            print(f"GENOS INFO: Camera-Baking map to: {target_filename}...")
            
            if _render_material_via_camera(context, temp_mat, size, filepath, use_alpha):
                saved_count += 1
            
            bpy.data.materials.remove(temp_mat)

        data_exports = (
            (getattr(mat, 'genos_normal_map', None) or get_img('Normal_Tex'), getattr(s, 'genos_exp_suf_normal', '_Normal'), 'NORMAL'),
            (getattr(mat, 'genos_roughness_map', None) or get_img('Roughness Map'), getattr(s, 'genos_exp_suf_roughness', '_Roughness'), 'ROUGHNESS'),
            (getattr(mat, 'genos_metallic_map', None) or get_img('Metallic Map'), getattr(s, 'genos_exp_suf_metallic', '_Metallic'), 'METALLIC'),
            (getattr(mat, 'genos_opacity_map', None) or get_img('Opacity Map'), getattr(s, 'genos_exp_suf_opacity', '_Opacity'), 'OPACITY'),
            (getattr(mat, 'genos_ao_map', None) or get_img('AO Map'), getattr(s, 'genos_exp_suf_ao', '_AO'), 'AO'),
            (getattr(mat, 'genos_displacement_map', None) or get_img('Displacement Map'), getattr(s, 'genos_exp_suf_displacement', '_Displacement'), 'DISPLACEMENT'),
        )
        for img, suffix, kind in data_exports:
            if not is_valid_image(img):
                continue
            if kind == 'DISPLACEMENT' and not getattr(s, 'genos_bake_displacement', False):
                # An assigned displacement map is still a valid input map; export it
                # when present even if auto-baking is disabled.
                pass
            configure_standard_map_image(img, kind)
            target_name = f"{mat_base}{suffix}.png"
            if save_image(img, out_dir, target_name):
                saved_count += 1

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
                        set_image_colorspace(i, 'Non-Color' if non_color else 'sRGB')
                        return i
                try: 
                    i = bpy.data.images.load(filepath)
                    set_image_colorspace(i, 'Non-Color' if non_color else 'sRGB')
                    return i
                except: return None
            
            b_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_albedo', '_BaseColor')}.png")
            i_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_ilm', '_ILM')}.png")
            d_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_detail', '_Detail')}.png")
            e_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_emission', '_Emission')}.png")
            sdf_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_sdf', '_SDF')}.png")
            n_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_normal', '_Normal')}.png")
            r_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_roughness', '_Roughness')}.png")
            m_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_metallic', '_Metallic')}.png")
            o_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_opacity', '_Opacity')}.png")
            ao_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_ao', '_AO')}.png")
            disp_path = os.path.join(out_dir, f"{mat_base}{getattr(s, 'genos_exp_suf_displacement', '_Displacement')}.png")
            
            b_img = get_or_load(b_path)
            i_img = get_or_load(i_path, True)
            d_img = get_or_load(d_path, True)
            e_img = get_or_load(e_path)
            sdf_img = get_or_load(sdf_path, True) if mat.get("genos_shader_type") == 'FACE' else None
            
            n_img = get_or_load(n_path, True) if os.path.exists(n_path) else getattr(mat, 'genos_normal_map', None)
            r_img = get_or_load(r_path, True) if os.path.exists(r_path) else getattr(mat, 'genos_roughness_map', None)
            m_img = get_or_load(m_path, True) if os.path.exists(m_path) else getattr(mat, 'genos_metallic_map', None)
            o_img = get_or_load(o_path, True) if os.path.exists(o_path) else getattr(mat, 'genos_opacity_map', None)
            ao_img = get_or_load(ao_path, True) if os.path.exists(ao_path) else getattr(mat, 'genos_ao_map', None)
            dp_img = get_or_load(disp_path, True) if os.path.exists(disp_path) else getattr(mat, 'genos_displacement_map', None)
            try:
                build_baked_material(
                    baked_mat, b_img, e_img, n_img, i_img, d_img, sdf_img, dp_img,
                    get_img("Pattern Mask"), roughness_img=r_img, metallic_img=m_img,
                    opacity_img=o_img, ao_img=ao_img
                )
            except Exception as e:
                print(e)

        self.report({'INFO'}, f"Success! Exported {saved_count} maps for shader '{mat_base}'.")
        return {'FINISHED'}
    
class GENOS_OT_add_outline(bpy.types.Operator):
    bl_idname = "genos.add_outline"
    bl_label = "Add / Update Anime Outline"
    bl_description = "Creates a crisp anime inverted hull outline with a single-color Emission node and true backface culling"

    def execute(self, context):
        targets = [o for o in context.selected_objects if o.type == 'MESH']
        if not targets and context.active_object and context.active_object.type == 'MESH':
            targets = [context.active_object]

        if not targets:
            self.report({'WARNING'}, "Please select at least one mesh object.")
            return {'CANCELLED'}

        scene = context.scene
        thickness = float(getattr(scene, "genos_outline_thickness", 0.004))
        color = tuple(getattr(scene, "genos_outline_color", (0.08, 0.04, 0.06, 1.0)))

        mat_name = "AnimeToon_Outline"
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            mat = bpy.data.materials.new(mat_name)
            mat.use_nodes = True

        mat.use_backface_culling = True
        try:
            mat.blend_method = 'OPAQUE'
        except Exception:
            pass

        # Simple inverted hull node tree: Emission -> Material Output
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        out = nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)

        emit = nodes.new("ShaderNodeEmission")
        emit.name = "Outline Emission"
        emit.location = (0, 0)
        emit.inputs["Color"].default_value = color
        emit.inputs["Strength"].default_value = 1.0

        links.new(emit.outputs["Emission"], out.inputs["Surface"])

        applied_count = 0
        for obj in targets:
            if mat.name not in [slot.name for slot in obj.material_slots]:
                obj.data.materials.append(mat)

            mat_idx = list(obj.data.materials).index(mat)

            mod = obj.modifiers.get("Anime Outline")
            if not mod:
                mod = obj.modifiers.new("Anime Outline", 'SOLIDIFY')

            mod.use_flip_normals = True
            mod.use_rim = False             # Essential: Prevents black caps on hair cards and open meshes
            mod.material_offset = mat_idx
            mod.material_offset_rim = mat_idx
            mod.offset = 1.0
            mod.thickness = thickness
            mod.use_even_offset = False      # Prevents spikes shooting out at acute corners
            mod.use_quality_normals = True
            applied_count += 1

        self.report({'INFO'}, f"Anime Outline applied to {applied_count} object(s) ({thickness*1000:.1f}mm lineart)!")
        return {'FINISHED'}

class GENOS_OT_fork_material_selection(bpy.types.Operator):
    bl_idname = "genos.fork_material_selection"
    bl_label = "Fork Material to Selection"
    bl_description = "Duplicate active anime material and assign it specifically to selected faces in Edit Mode"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh.")
            return {'CANCELLED'}

        orig_mode = obj.mode
        if orig_mode != 'EDIT':
            self.report({'WARNING'}, "Switch to Edit Mode and select faces first.")
            return {'CANCELLED'}

        active_mat = obj.active_material
        if not active_mat or not active_mat.use_nodes:
            self.report({'ERROR'}, "No active material found to fork.")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({'WARNING'}, "No faces selected in Edit Mode! Select faces to apply effect.")
            return {'CANCELLED'}

        preset = getattr(context.scene, "genos_selection_fork_preset", "TECH_GLOW")
        preset_suffix = {
            "TECH_GLOW": "TechGlow",
            "MECHA_ARMOR": "Mecha",
            "HAIR_ACCENT": "HairAccent",
            "CLOTHING_PATTERN": "Pattern",
            "CUSTOM_VARIANT": "Variant"
        }.get(preset, "Selection")

        # Duplicate the active material
        new_mat = active_mat.copy()
        new_mat.name = f"{active_mat.name}_{preset_suffix}"

        # Configure preset specific settings on new_mat
        if preset == "MECHA_ARMOR":
            new_mat["genos_spec_metallic"] = True
            spec_mult = new_mat.node_tree.nodes.get("Anime Spec Core Multiplier")
            if spec_mult and len(spec_mult.inputs) > 1:
                spec_mult.inputs[1].default_value = 2.5
        elif preset == "TECH_GLOW":
            glow_node = new_mat.node_tree.nodes.get("Anime Tech Emission Tint")
            if glow_node:
                try:
                    glow_node.inputs[1].default_value = (1.0, 0.45, 0.1, 1.0)
                except Exception:
                    pass

        # Append new material to mesh material slots
        obj.data.materials.append(new_mat)
        new_slot_idx = len(obj.data.materials) - 1

        # Assign selected faces to the new material slot
        for f in selected_faces:
            f.material_index = new_slot_idx

        bmesh.update_edit_mesh(obj.data)
        obj.active_material_index = new_slot_idx

        self.report({'INFO'}, f"Forked material '{new_mat.name}' assigned to {len(selected_faces)} selected faces!")
        return {'FINISHED'}

class GENOS_OT_make_material_unique(bpy.types.Operator):
    bl_idname = "genos.make_material_unique"
    bl_label = "Make Material Unique for Object"
    bl_description = "Make materials on this object single-user so changes and effects don't affect other objects sharing them"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh.")
            return {'CANCELLED'}

        if not obj.material_slots:
            self.report({'WARNING'}, "Object has no material slots.")
            return {'CANCELLED'}

        count = 0
        for slot in obj.material_slots:
            if slot.material and slot.material.users > 1:
                old_name = slot.material.name
                slot.material = slot.material.copy()
                slot.material.name = f"{old_name}_{obj.name}"
                count += 1

        if count > 0:
            self.report({'INFO'}, f"Made {count} material(s) unique on object '{obj.name}'.")
        else:
            self.report({'INFO'}, f"Materials on '{obj.name}' are already unique.")
        return {'FINISHED'}

class GENOS_OT_fill_selection_mask(bpy.types.Operator):
    bl_idname = "genos.fill_selection_mask"
    bl_label = "Fill Mask on Selected Faces"
    bl_description = "Fill effect/mask value onto selected faces in Edit Mode for the active target texture"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh.")
            return {'CANCELLED'}

        mat = obj.active_material
        if not mat or not mat.use_nodes:
            self.report({'ERROR'}, "No active material found.")
            return {'CANCELLED'}

        s = context.scene
        target_key = getattr(s, "genos_selection_effect_target", "ILM_EMISSION")
        fill_val = getattr(s, "genos_selection_fill_value", 1.0)
        fill_col = getattr(s, "genos_selection_fill_color", (1.0, 1.0, 1.0, 1.0))

        target_node_map = {
            "ILM_EMISSION": "ILM_Emission",
            "ILM_SPEC": "ILM_Spec",
            "PATTERN_MASK": "Pattern Mask",
            "ILM_RIM": "ILM_Rim",
            "ILM_SHADOW": "ILM_Shadow",
            "DETAIL_ACCENT": "Detail_Accent",
            "BASECOLOR": "BaseColor"
        }
        node_name = target_node_map.get(target_key, "ILM_Emission")

        node = mat.node_tree.nodes.get(node_name)
        if target_key == "PATTERN_MASK" and (not node or not getattr(node, "image", None)):
            img = ensure_source_image(mat, "Pattern Mask", "PatternMask", (0.0, 0.0, 0.0, 1.0), MASK_COLORSPACE)
            if node: node.image = img
        elif target_key in ("ILM_EMISSION", "ILM_SPEC", "ILM_RIM", "ILM_SHADOW") and (not node or not getattr(node, "image", None)):
            default_colors = {
                "ILM_EMISSION": (0.0, 0.0, 0.0, 1.0),
                "ILM_SPEC": (0.0, 0.0, 0.0, 1.0),
                "ILM_RIM": (0.0, 0.0, 0.0, 1.0),
                "ILM_SHADOW": (0.5, 0.5, 0.5, 1.0)
            }
            img = ensure_source_image(mat, node_name, node_name.replace(" ", ""), default_colors.get(target_key, (0.0, 0.0, 0.0, 1.0)), MASK_COLORSPACE)
            if node: node.image = img

        target_img = node.image if node and hasattr(node, "image") else None
        if not target_img:
            self.report({'ERROR'}, f"Target image node '{node_name}' has no image assigned.")
            return {'CANCELLED'}

        fill_color_tuple = tuple(fill_col) if target_key == "BASECOLOR" else (fill_val, fill_val, fill_val, 1.0)

        n_faces = rasterize_uv_faces_to_image(obj, target_img, fill_color=fill_color_tuple, target_key=target_key)
        if n_faces == 0:
            self.report({'WARNING'}, "No faces selected to fill! Select faces in Edit Mode first.")
            return {'CANCELLED'}

        if target_key in ("ILM_EMISSION", "ILM_SPEC", "ILM_RIM", "ILM_SHADOW"):
            pack_material_ilm(mat)
        elif target_key == "DETAIL_ACCENT":
            pack_material_detail(mat)

        self.report({'INFO'}, f"Filled {target_key} on {n_faces} selected faces!")
        return {'FINISHED'}

class GENOS_OT_set_vertex_fx_mask(bpy.types.Operator):
    bl_idname = "genos.set_vertex_fx_mask"
    bl_label = "Set Vertex FX on Selection"
    bl_description = "Assign Anime_FX Color Attribute to selected faces (or whole object) for per-mesh glow, mecha, or rim effects"

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh.")
            return {'CANCELLED'}

        channel = getattr(context.scene, "genos_selection_vertex_fx_channel", "GLOW")
        channel_color = {
            "GLOW": (1.0, 0.0, 0.0, 1.0),       # Red = Tech Glow
            "SPEC_MECHA": (0.0, 1.0, 0.0, 1.0), # Green = Mecha Armor
            "RIM": (0.0, 0.0, 1.0, 1.0),        # Blue = Rim Boost
            "CLEAR": (0.0, 0.0, 0.0, 1.0)       # Black = Neutral / No FX
        }.get(channel, (1.0, 0.0, 0.0, 1.0))

        orig_mode = obj.mode
        is_edit = (orig_mode == 'EDIT')

        attr_name = "Anime_FX"
        color_attr = obj.data.color_attributes.get(attr_name)
        if not color_attr:
            color_attr = obj.data.color_attributes.new(
                name=attr_name,
                type='BYTE_COLOR',
                domain='CORNER'
            )

        if is_edit:
            bm = bmesh.from_edit_mesh(obj.data)
            color_layer = bm.loops.layers.color.get(attr_name)
            if not color_layer:
                color_layer = bm.loops.layers.color.new(attr_name)

            selected_faces = [f for f in bm.faces if f.select]
            if not selected_faces:
                self.report({'WARNING'}, "No faces selected in Edit Mode.")
                return {'CANCELLED'}

            for f in selected_faces:
                for loop in f.loops:
                    loop[color_layer] = channel_color

            bmesh.update_edit_mesh(obj.data)
            self.report({'INFO'}, f"Set {channel} Vertex FX on {len(selected_faces)} selected faces!")
        else:
            mesh = obj.data
            for i in range(len(mesh.loops)):
                color_attr.data[i].color = channel_color
            mesh.update()
            self.report({'INFO'}, f"Set {channel} Vertex FX on entire object '{obj.name}'!")

        return {'FINISHED'}

class GENOS_OT_set_material_shader_type(bpy.types.Operator):
    bl_idname = "genos.set_material_shader_type"
    bl_label = "Set Material Shader Role"
    bl_description = "Assign active material role (Face, Hair, Default, Metallic) and rebuild node tree"
    shader_type: bpy.props.StringProperty(name="Shader Type", default="DEFAULT")

    def execute(self, context):
        obj = context.active_object
        if not obj or not obj.active_material:
            self.report({'ERROR'}, "No active material.")
            return {'CANCELLED'}
        mat = obj.active_material
        mat["genos_shader_type"] = self.shader_type
        try:
            bpy.ops.genos.regenerate_shader()
        except Exception as e:
            print("Regenerate error:", e)
        self.report({'INFO'}, f"Material '{mat.name}' set to {self.shader_type}")
        return {'FINISHED'}

class GENOS_OT_auto_classify_materials(bpy.types.Operator):
    bl_idname = "genos.auto_classify_materials"
    bl_label = "Auto-Classify All Materials"
    bl_description = "Automatically detect Face, Hair, and Body materials across all slots and rebuild shaders"

    def execute(self, context):
        obj = context.active_object
        if not obj or not obj.material_slots:
            self.report({'ERROR'}, "No active mesh object with material slots.")
            return {'CANCELLED'}
        count = 0
        classified = []
        orig_idx = obj.active_material_index
        for idx, slot in enumerate(obj.material_slots):
            if slot.material:
                mat = slot.material
                detected = detect_shader_type_for_material(mat)
                mat["genos_shader_type"] = detected
                classified.append(f"{mat.name} -> {detected}")
                if "is_anime_toon" in mat or "is_anime_toon_baked" in mat:
                    try:
                        obj.active_material_index = idx
                        bpy.ops.genos.regenerate_shader()
                    except Exception as e:
                        print(f"Error rebuilding {mat.name}: {e}")
                count += 1
        obj.active_material_index = orig_idx
        self.report({'INFO'}, f"Classified {count} material(s): {', '.join(classified[:4])}")
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
        box.operator("genos.adapt_all_shaders", icon='SHADING_RENDERED', text="Adapt All Shaders to New Scheme")

        if mat and mat.use_nodes and ("is_anime_toon" in mat or "is_anime_toon_baked" in mat):
            layout.separator()
            sh_type = mat.get("genos_shader_type", "DEFAULT")
            
            layout.label(text=f"Editing Shader: {mat.name}", icon='MATERIAL')

            # Material Role Management
            role_box = layout.box()
            role_icon = 'USER' if sh_type == 'FACE' else ('STRANDS' if sh_type == 'HAIR' else 'MATERIAL')
            role_box.label(text=f"Material Role: {sh_type}", icon=role_icon)
            role_row = role_box.row(align=True)
            for r_name, r_icon in [("FACE", 'USER'), ("HAIR", 'STRANDS'), ("DEFAULT", 'MESH_CUBE'), ("METALLIC", 'SHADING_RENDERED')]:
                op = role_row.operator("genos.set_material_shader_type", text=r_name.title(), icon=r_icon, depress=(sh_type == r_name))
                op.shader_type = r_name
            role_box.operator("genos.auto_classify_materials", icon='AUTO', text="Auto-Detect Roles for All Mesh Slots")

            img_box = layout.box()
            img_box.label(text="Assign Core Maps", icon='COLOR')
            img_box.prop(s, "genos_emission_channel", text="Emission Channel")
            
            img_box.label(text="Base Color (Albedo / Alpha):")
            base_node = mat.node_tree.nodes.get("BaseColor")
            if getattr(mat, "genos_base_color_map", None) is not None:
                img_box.template_ID(mat, "genos_base_color_map", open="image.open")
            elif base_node:
                img_box.template_ID(base_node, "image", open="image.open")
            elif hasattr(mat, "genos_base_color_map"):
                img_box.template_ID(mat, "genos_base_color_map", open="image.open")
                
            img_box.label(text="Custom Emission:")
            emission_node = mat.node_tree.nodes.get("Emission Map")
            if getattr(mat, "genos_emission_map", None) is not None:
                img_box.template_ID(mat, "genos_emission_map", open="image.open")
            elif emission_node:
                img_box.template_ID(emission_node, "image", open="image.open")
            elif hasattr(mat, "genos_emission_map"):
                img_box.template_ID(mat, "genos_emission_map", open="image.open")

            img_box.label(text="Normal Map (Tangent Space):")
            img_box.template_ID(mat, "genos_normal_map", open="image.open")
            row = img_box.row(align=True)
            row.prop(s, "genos_normal_convention", text="Convention")
            row.prop(s, "genos_normal_strength", text="Strength")

            img_box.label(text="Roughness / Metallic:")
            row = img_box.row(align=True)
            row.template_ID(mat, "genos_roughness_map", open="image.open")
            row.template_ID(mat, "genos_metallic_map", open="image.open")

            img_box.label(text="Opacity / AO Data Maps:")
            row = img_box.row(align=True)
            row.template_ID(mat, "genos_opacity_map", open="image.open")
            row.template_ID(mat, "genos_ao_map", open="image.open")

            img_box.label(text="Packed ILM Map (RGBA):")
            img_box.template_ID(mat, "genos_ilm_packed", open="image.open")

            img_box.label(text="Packed Detail Map (RGBA):")
            img_box.template_ID(mat, "genos_detail_packed", open="image.open")

            pattern_node = mat.node_tree.nodes.get("Pattern Mask")
            if pattern_node:
                img_box.label(text="Pattern Mask:")
                img_box.template_ID(pattern_node, "image", open="image.open")
                img_box.label(text="Pattern Color Map:")
                img_box.template_ID(mat, "genos_pattern_color_map", open="image.open")

            img_box.label(text="Displacement Map:")
            img_box.template_ID(mat, "genos_displacement_map", open="image.open")

            if sh_type == 'FACE':
                sdf_node = mat.node_tree.nodes.get("SDF Map")
                if sdf_node:
                    img_box.label(text="Face SDF Map:")
                    img_box.template_ID(mat, "genos_sdf_map", open="image.open")

            # ----------------------------------------------------------------
            # Nikke / Wuwa / PGR 3-Banded Cel Shading Studio
            # ----------------------------------------------------------------
            cel_box = layout.box()
            cel_box.label(text="3-Banded Cel Shading (Nikke / Wuwa / PGR)", icon='SHADING_RENDERED')
            
            thresh_col = cel_box.column(align=True)
            row = thresh_col.row(align=True)
            row.prop(s, "genos_shadow_band1_thresh", text="Band 1 (Lit Key)")
            row.prop(s, "genos_shadow_band2_thresh", text="Band 2 (Deep)")
            thresh_col.prop(s, "genos_shadow_feather", text="Shadow Feather / Crispness")
            
            color_box = cel_box.box()
            color_box.label(text="Shadow Multiplier Tints:")
            row = color_box.row(align=True)
            row.prop(s, "genos_shadow_color_1", text="1st Shadow")
            row.prop(s, "genos_shadow_color_2", text="2nd Deep Shadow")
            
            term_box = cel_box.box()
            term_box.prop(s, "genos_terminator_fringe_enable", text="Enable SSS Terminator Fringe")
            if getattr(s, "genos_terminator_fringe_enable", True):
                row = term_box.row(align=True)
                row.prop(s, "genos_terminator_fringe_color", text="Fringe Color")
                row.prop(s, "genos_terminator_fringe_intensity", text="Intensity")

            # ----------------------------------------------------------------
            # ----------------------------------------------------------------
            # Anime Hair Studio (Universal Styling & Color Presets)
            # ----------------------------------------------------------------
            hair_box = layout.box()
            hair_box.label(text="Anime Hair Studio (Universal Hair Styling)", icon='CURVES')

            # Preset Selection & One-Click Actions
            p_box = hair_box.box()
            p_box.prop(s, "genos_hair_source_preservation")
            p_box.label(text="Anime Hair Color Palettes:")
            row = p_box.row(align=True)
            row.prop(s, "genos_hair_color_preset", text="")
            row.operator("genos.apply_hair_preset", text="Apply Preset", icon='CHECKMARK')

            q_row = p_box.row(align=True)
            q_row.operator("genos.apply_hair_preset_nikke", text="Nikke Red Match", icon='IMAGE_DATA')
            q_row.operator("genos.auto_harmonize_hair_color", text="Auto-Harmonize", icon='COLOR')

            # Independent realtime highlight bands
            hl_box = hair_box.box()
            hl_box.label(text="Independent Real-Time Hair Highlight Bands:")
            top = hl_box.row(align=True)
            top.label(text="Bands: %d" % len(s.genos_hair_bands))
            top.operator("genos.add_hair_band", text="Add Highlight Band", icon='ADD')
            hl_box.label(text="Each band owns shape, curve, opacity, color and ILM.G glow controls.")

            if len(s.genos_hair_bands) == 0:
                hl_box.operator("genos.add_hair_band", text="Create First Highlight Band", icon='ADD')
            else:
                for band_index, band in enumerate(s.genos_hair_bands):
                    band_box = hl_box.box()
                    header = band_box.row(align=True)
                    header.prop(band, 'enabled', text='')
                    header.prop(band, 'label', text='')
                    up = header.operator('genos.move_hair_band', text='', icon='TRIA_UP'); up.index=band_index; up.direction=-1
                    down = header.operator('genos.move_hair_band', text='', icon='TRIA_DOWN'); down.index=band_index; down.direction=1
                    dup = header.operator('genos.duplicate_hair_band', text='', icon='DUPLICATE'); dup.index=band_index
                    rem = header.operator('genos.remove_hair_band', text='', icon='X'); rem.index=band_index

                    row = band_box.row(align=True)
                    row.prop(band, 'position', text='Position'); row.prop(band, 'width', text='Width'); row.prop(band, 'strength', text='Strength')
                    row = band_box.row(align=True)
                    row.prop(band, 'opacity', text='Opacity'); row.prop(band, 'emission_enabled', text='ILM.G Glow'); row.prop(band, 'emission_strength', text='Glow Strength')
                    band_box.prop(band, 'color', text='Band / Glow Color')
                    row = band_box.row(align=True)
                    row.prop(band, 'spot_shape', text='Shape'); row.prop(band, 'spot_density', text='Density'); row.prop(band, 'spot_gap', text='Gap')
                    if band.spot_shape == 'OVAL': band_box.prop(band, 'spot_aspect', text='Oval Aspect')
                    row = band_box.row(align=True)
                    row.prop(band, 'softness', text='Edge Softness'); row.prop(band, 'blur', text='Blur / Feather'); row.prop(band, 'strand_detail', text='Strand Detail')
                    row = band_box.row(align=True)
                    row.prop(band, 'curvature', text='Crown Curve'); row.prop(band, 'curve_center', text='Curve Center')
                    band_box.prop(band, 'coord_mode', text='Elevation Source')

            master = hl_box.box()
            master.label(text='Master Hair Specular (not band shape controls):')
            row = master.row(align=True)
            row.prop(s, "genos_hair_strands_strength", text="Source Strand Glints")
            row.prop(s, "genos_hair_strands_scale", text="Source Strand Density")
            row = master.row(align=True)
            row.prop(s, "genos_hair_highlight_strength", text="Master Highlight Strength")
            row.prop(s, "genos_hair_pbr_spec_str", text="PBR Core Glint")
            master.prop(s, "genos_hair_highlight_tint", text="Core Glint Tint")

            # Unlimited dynamic hair toon shading bands
            h_cel_box = hair_box.box()
            h_cel_box.label(text="Dynamic Hair Toon Shading Bands:")
            top = h_cel_box.row(align=True)
            top.label(text="Toon Bands: %d" % len(s.genos_hair_toon_bands))
            top.operator('genos.add_hair_toon_band', text='Add Toon Band', icon='ADD')
            h_cel_box.label(text="Order: top = shallow/lit-side shadow, bottom = deepest shadow.")
            if len(s.genos_hair_toon_bands) == 0:
                h_cel_box.operator('genos.add_hair_toon_band', text='Create First Toon Band', icon='ADD')
            else:
                for toon_index, toon in enumerate(s.genos_hair_toon_bands):
                    box = h_cel_box.box()
                    header = box.row(align=True)
                    header.prop(toon, 'label', text='')
                    up = header.operator('genos.move_hair_toon_band', text='', icon='TRIA_UP'); up.index=toon_index; up.direction=-1
                    down = header.operator('genos.move_hair_toon_band', text='', icon='TRIA_DOWN'); down.index=toon_index; down.direction=1
                    dup = header.operator('genos.duplicate_hair_toon_band', text='', icon='DUPLICATE'); dup.index=toon_index
                    rem = header.operator('genos.remove_hair_toon_band', text='', icon='X'); rem.index=toon_index
                    row = box.row(align=True)
                    row.prop(toon, 'threshold', text='Threshold'); row.prop(toon, 'feather', text='Feather'); row.prop(toon, 'strength', text='Tint Strength')
                    box.prop(toon, 'color', text='Shadow Tint')

            # Hair Tip Ombre Depth Gradient
            tip_box = hair_box.box()
            tip_box.label(text="Hair Tip Ombre Studio:", icon='MOD_EDGES')

            # One-Click Detection and Baking Operators
            btn_row = tip_box.row(align=True)
            btn_row.operator("genos.detect_and_apply_hair_ombre", text="Detect & Apply Ombre", icon='CURVES')
            btn_row.operator("genos.bake_hair_ombre", text="Bake Ombre", icon='RENDER_STILL')

            # Coordinate Source Mode
            tip_box.prop(s, "genos_hair_ombre_coord_mode", text="Source")

            # Color & Blend Mode Controls
            col_row = tip_box.row(align=True)
            col_row.prop(s, "genos_hair_tip_tint", text="Ombre Tint")
            col_row.prop(s, "genos_hair_ombre_blend", text="")

            slider_row = tip_box.row(align=True)
            slider_row.prop(s, "genos_hair_ombre_range", text="Spread")
            slider_row.prop(s, "genos_hair_ombre_power", text="Falloff Power")

            tip_box.prop(s, "genos_hair_tip_strength", text="Master Intensity")

            # ----------------------------------------------------------------
            # Anime Specular & Clothing Studio (2D Matte Fabric / Crisp Cel Leather)
            # ----------------------------------------------------------------
            spec_style_box = layout.box()
            spec_style_box.label(text="Anime Specular & Clothing Studio", icon='SHADING_RENDERED')

            # Clothing Shading Mode
            spec_style_box.prop(s, "genos_cloth_spec_mode", text="Cloth Spec Mode")

            # 1-Click Material Presets Row
            cloth_p_row = spec_style_box.row(align=True)
            op_m = cloth_p_row.operator("genos.apply_cloth_preset", text="Matte Fabric", icon='MOD_CLOTH')
            op_m.mode = "ANIME_MATTE"
            op_l = cloth_p_row.operator("genos.apply_cloth_preset", text="Crisp Leather", icon='BRUSH_DATA')
            op_l.mode = "CRISP_CEL"
            op_a = cloth_p_row.operator("genos.apply_cloth_preset", text="Metallic Armor", icon='MATERIAL')
            op_a.mode = "METALLIC"
            op_h = cloth_p_row.operator("genos.apply_cloth_preset", text="Hybrid Auto", icon='AUTO')
            op_h.mode = "HYBRID_AUTO"

            # 2D Matte Fabric Velvet Lobe
            velvet_sub = spec_style_box.box()
            velvet_sub.label(text="2D Matte Fabric Velvet Sheen (Cotton/Wool/Silk):")
            row = velvet_sub.row(align=True)
            row.prop(s, "genos_cloth_velvet_sheen", text="Velvet Sheen")
            row.prop(s, "genos_cloth_velvet_power", text="Falloff Power")

            # Crisp Cel Glints & Armor
            cel_sub = spec_style_box.box()
            cel_sub.label(text="Crisp Anime Cel Glints & Armor (Belts/Vinyl/Metals):")
            row = cel_sub.row(align=True)
            row.prop(s, "genos_cloth_spec_str", text="Cloth Cel Glint")
            row.prop(s, "genos_spec_core_strength", text="Core Intensity")
            row = cel_sub.row(align=True)
            row.prop(s, "genos_spec_tint", text="Spec Tint")
            row.prop(s, "genos_spec_metallic", text="Metallic Mode", toggle=True)

            # Shadow Ambient Bounce (Prevents dead gray shadows on cloth)
            bounce_sub = spec_style_box.box()
            bounce_sub.label(text="Clothing Shadow Ambient Bounce Tint:")
            bounce_sub.prop(s, "genos_cloth_shadow_bounce", text="Shadow Ambient Bounce")

            # ----------------------------------------------------------------
            # Anime Lighting & Tech Special Effects
            # ----------------------------------------------------------------
            fx_style_box = layout.box()
            fx_style_box.label(text="Anime Lighting & Tech Special Effects", icon='LIGHT_HEMI')
            
            rim_sub = fx_style_box.box()
            rim_sub.label(text="Directional Backlight Rim:")
            row = rim_sub.row(align=True)
            row.prop(s, "genos_rim_color", text="Rim Color")
            row.prop(s, "genos_rim_intensity", text="Intensity")
            row = rim_sub.row(align=True)
            row.prop(s, "genos_rim_power", text="Power / Tightness")
            row.prop(s, "genos_rim_directional", text="Directional Mode", toggle=True)
            
            emit_sub = fx_style_box.box()
            emit_sub.label(text="Sci-Fi Tech Emission & Pulse Glow:")
            row = emit_sub.row(align=True)
            row.prop(s, "genos_emission_channel", text="Channel")
            row.prop(s, "genos_emission_map_strength", text="Strength")
            row.prop(s, "genos_emission_tint", text="Tech Tint")
            row = emit_sub.row(align=True)
            row.prop(s, "genos_emission_pulse_enable", text="Pulse Glow", toggle=True)
            if getattr(s, "genos_emission_pulse_enable", False):
                row.prop(s, "genos_emission_pulse_speed", text="Pulse Speed")

            # Quick update button
            layout.operator("genos.regenerate_shader", icon='FILE_REFRESH', text="Apply Styling to Active Shader")

            # ----------------------------------------------------------------
            # RGB Shadow Map → ILM Converter (Legacy Older Toon Shader Support)
            # ----------------------------------------------------------------
            conv_box = layout.box()
            conv_box.label(text="Legacy Shadow Map → ILM Converter", icon='IMPORT')
            conv_box.prop(s, "genos_rgb_shadow_mode", text="Format")
            if getattr(s, "genos_rgb_shadow_mode", "COLORZONE") == "COLORZONE":
                conv_box.label(
                    text="Flat-color zone map: luminance = shadow, zone edges = rim.",
                    icon='INFO'
                )
            else:
                conv_box.label(
                    text="Per-channel: R=outline, G=shadow, B=spec, A=rim.",
                    icon='INFO'
                )
            conv_box.prop(s, "genos_rgb_shadow_map", text="Shadow Map")
            conv_row = conv_box.row(align=True)
            conv_row.prop(s, "genos_rgb_shadow_spec_threshold", text="Spec Threshold")
            conv_row.prop(s, "genos_rgb_shadow_invert", text="Invert", toggle=True)
            conv_box.operator(
                "genos.convert_rgb_shadow_to_ilm",
                icon='NODE_MATERIAL',
                text="Convert Shadow Map → ILM"
            )
            conv_box.label(
                text="After converting, click 'Clean/Regen Shader' to update viewport.",
                icon='INFO'
            )

            # ----------------------------------------------------------------
            # Selection & Multi-Object Isolation Studio
            # ----------------------------------------------------------------
            iso_box = layout.box()
            iso_box.label(text="Selection & Multi-Object Isolation", icon='MOD_MASK')

            is_edit = (obj.mode == 'EDIT')

            # Section 1: Edit-Mode Face Selection Material Forking
            fork_box = iso_box.box()
            fork_box.label(text="1. Material Slot Forking (Per-Selection / Per-Object):", icon='MATERIAL')
            if is_edit:
                fork_box.label(text="Mode: EDIT (Applies to Selected Faces)", icon='EDITMODE_HLT')
                row = fork_box.row(align=True)
                row.prop(s, "genos_selection_fork_preset", text="")
                row.operator("genos.fork_material_selection", icon='DUPLICATE', text="Fork to Selection")
            else:
                fork_box.label(text="Mode: OBJECT (Isolate Shared Materials)", icon='OBJECT_DATAMODE')
                fork_box.operator("genos.make_material_unique", icon='DUPLICATE', text="Make Material Unique (Object)")
                fork_box.label(text="Tip: Enter Edit Mode & select faces to fork to specific parts.", icon='INFO')

            # Section 2: Direct Texture Mask Fill on Selected Faces
            fill_box = iso_box.box()
            fill_box.label(text="2. Direct Mask Fill on Selection (UV Faces):", icon='BRUSH_DATA')
            fill_box.prop(s, "genos_selection_effect_target", text="Target")
            if getattr(s, "genos_selection_effect_target", "ILM_EMISSION") == "BASECOLOR":
                fill_box.prop(s, "genos_selection_fill_color", text="Color")
            else:
                fill_box.prop(s, "genos_selection_fill_value", text="Mask Value")
            row = fill_box.row(align=True)
            row.operator("genos.fill_selection_mask", icon='COLORSET_02_VEC', text="Fill Effect on Selection")

            # Section 3: Mesh Color Attribute (Vertex FX Mask)
            vfx_box = iso_box.box()
            vfx_box.label(text="3. Mesh Vertex FX Attribute (Anime_FX):", icon='COLOR')
            vfx_box.prop(s, "genos_use_vertex_fx_mask", text="Enable Vertex FX in Shader")
            row = vfx_box.row(align=True)
            row.prop(s, "genos_selection_vertex_fx_channel", text="")
            row.operator("genos.set_vertex_fx_mask", icon='VPAINT_HLT', text="Set Vertex FX")
            vfx_box.label(text="Lives on mesh data; works across objects sharing the same material.", icon='INFO')

            paint_box = layout.box()
            paint_box.label(text="LIVE Texture Painter", icon='BRUSH_DATA')
            paint_box.prop(s, "genos_paint_target", text="")
            row = paint_box.row(align=True)
            row.prop(s, "genos_autotoggle_paint", text="Auto-Switch Mode", icon='BRUSH_DATA')
            row.operator("genos.set_paint_target", icon='RESTRICT_SELECT_OFF', text="Start Painting")

            tools_box = layout.box()
            outline_box = tools_box.box()
            outline_box.label(text="Anime Outline (Inverted Hull):", icon='MOD_SOLIDIFY')
            outline_box.operator("genos.add_outline", icon='MOD_SOLIDIFY', text="Add / Update Anime Outline")
            row = outline_box.row(align=True)
            row.prop(s, "genos_outline_thickness", text="Thickness")
            row.prop(s, "genos_outline_color", text="")
            row = tools_box.row(align=True)
            row.operator("genos.regenerate_shader", icon='FILE_REFRESH', text="Clean/Regen Active")
            row.operator("genos.adapt_all_shaders", icon='AUTO', text="Adapt All Shaders")
            tools_box.operator("genos.bake_preview_appearance", icon='RENDER_STILL', text="Bake Preview Appearance")
            
            row = tools_box.row(align=True)
            row.operator("genos.bake_ao", icon='SHADING_RENDERED', text="Bake AO")
            
            curve_box = tools_box.box()
            curve_box.label(text="Auto-Generate Edge Lineart:")
            curve_box.prop(s, "genos_lineart_preset", text="Preset")
            row = curve_box.row(align=True)
            row.prop(s, "genos_lineart_radius", text="Radius")
            curve_box.prop(s, "genos_lineart_samples", text="Samples")
            edge_row = curve_box.row(align=True)
            edge_row.prop(s, "genos_lineart_edge_min", text="Edge Min")
            edge_row.prop(s, "genos_lineart_edge_max", text="Edge Max")
            curve_box.prop(s, "genos_lineart_gamma", text="Sharpness")
            curve_box.prop(s, "genos_lineart_smooth", text="Smooth Sharp Bake")
            row.operator("genos.bake_curvature", icon='MATCLOTH', text="Bake Lineart")

            pattern_box = tools_box.box()
            pattern_box.label(text="Clothing Pattern Layer:")
            pattern_box.prop(s, "genos_clothing_pattern_type", text="Type")
            pattern_box.prop(s, "genos_pattern_scale", text="Scale")
            pattern_box.prop(s, "genos_pattern_rotation", text="Rotation")
            pattern_box.prop(s, "genos_pattern_strength", text="Strength")
            pattern_box.prop(s, "genos_pattern_tint", text="Tint")
            pattern_box.label(text="Use Pattern Mask paint target to place layer on mesh regions.", icon='INFO')

            cache_box = pattern_box.box()
            cache_box.label(text="Offline Pattern Library:")
            cache_box.prop(s, "genos_pattern_cache_dir", text="Cache Dir")
            row = cache_box.row(align=True)
            row.operator("genos.download_all_pattern_presets", icon='IMPORT', text="Download All")

            preset_map = [
                ("PANTYHOSE", "Pantyhose"),
                ("STRIPES", "Stripes"),
                ("RIPPED", "Ripped"),
                ("BODYSUIT_HEX", "Bodysuit Hex"),
                ("DOTS", "Dots"),
                ("COTTON", "Cotton"),
                ("LEATHER", "Leather"),
            ]
            for key, label in preset_map:
                row = cache_box.row(align=True)
                d = row.operator("genos.download_pattern_preset", text=f"Download {label}", icon='IMPORT')
                d.pattern_key = key
                a = row.operator("genos.apply_cached_pattern_preset", text="Apply", icon='CHECKMARK')
                a.pattern_key = key

            src_box = cache_box.box()
            src_box.label(text="Source ZIP URLs (editable):")
            src_box.prop(s, "genos_pattern_url_pantyhose", text="Pantyhose")
            src_box.prop(s, "genos_pattern_url_stripes", text="Stripes")
            src_box.prop(s, "genos_pattern_url_ripped", text="Ripped")
            src_box.prop(s, "genos_pattern_url_bodysuit", text="Bodysuit")
            src_box.prop(s, "genos_pattern_url_dots", text="Dots")
            src_box.prop(s, "genos_pattern_url_cotton", text="Cotton")
            src_box.prop(s, "genos_pattern_url_leather", text="Leather")
            if getattr(s, "genos_pattern_last_download_report", ""):
                cache_box.label(text=f"Last: {s.genos_pattern_last_download_report}", icon='INFO')
            spec_box = tools_box.box()
            spec_box.label(text="Auto-Generate Specular:")
            row = spec_box.row(align=True)
            row.prop(s, "genos_spec_mat_type", text="")
            row.operator("genos.bake_specular", icon='LIGHT_SUN', text="Bake Specular")

            fx_box = tools_box.box()
            fx_box.label(text="Anime Hair/Eye FX to ILM + Detail:")
            row = fx_box.row(align=True)
            row.prop(s, "genos_hair_highlight_strength", text="Hair Strength")
            row.prop(s, "genos_hair_strands_strength", text="Strands")
            row = fx_box.row(align=True)
            row.prop(s, "genos_hair_tip_strength", text="Tip Ombre")
            row.prop(s, "genos_eye_sparkle_strength", text="Eye Strength")
            fx_box.operator("genos.bake_anime_fx", icon='SHADING_TEXTURE', text="Bake Anime Hair/Eye FX")
            
            disp_box = tools_box.box()
            disp_box.label(text="Displacement / Depth:")
            disp_box.prop(s, "genos_bake_displacement", text="Bake Displacement")
            row = disp_box.row(align=True)
            row.prop(s, "genos_displacement_strength", text="Strength / Scale")
            row.prop(s, "genos_displacement_midlevel", text="Midlevel")
            disp_box.prop(s, "genos_true_displacement", text="Cycles True Displacement")
            disp_box.operator("genos.bake_displacement", icon='MOD_DISPLACE', text="Bake Current Height Signal")

            norm_box = tools_box.box()
            norm_box.label(text="Normal & Standard Data Maps:")
            row = norm_box.row(align=True)
            row.prop(s, "genos_normal_convention", text="Normal")
            row.prop(s, "genos_normal_strength", text="Strength")
            norm_box.operator("genos.bake_normal", icon='MOD_NORMALEDIT', text="Bake Final Normal Details")
            norm_box.operator("genos.bake_standard_data_maps", icon='SHADING_TEXTURE', text="Bake Roughness / Metallic / Opacity / AO")

            # --- BULLETPROOF SDF UI CHECK ---
            # Checks if the node actually exists in the active material
            if mat.node_tree.nodes.get("SDF Map"):
                sdf_box = tools_box.box()
                sdf_box.label(text="Auto-Generate Face SDF:")
                sdf_box.operator("genos.bake_sdf", icon='LIGHT_HEMI', text="Bake SDF Baseline")
            
            live_box = layout.box()
            live_box.label(text="Live Texture Packing", icon='TEXTURE')
            live_row = live_box.row(align=True)
            live_row.operator("genos.pack_ilm", icon='MOD_HUE_SATURATION', text="Pack ILM Maps")
            live_row.operator("genos.pack_detail", icon='MOD_HUE_SATURATION', text="Pack Detail Maps")
            
            pack_box = layout.box()
            pack_box.label(text="Final Game Export", icon='EXPORT')
            
            # FIXED: Add the Mesh Export UI Toggle right here
            pack_box.prop(s, "genos_autobake_fx_on_export", text="Auto-Bake Material FX on Export")
            pack_box.prop(s, "genos_export_mesh_copy", text="Create Baked Mesh Copy")
            
            pack_box.label(text="Dynamically exports based on current Material Name", icon='INFO')
            
            name_box = pack_box.box()
            name_box.label(text="Target Output Suffixes:", icon='FILE_BLANK')
            name_col = name_box.column(align=True)
            name_col.prop(s, "genos_exp_suf_albedo", text="BaseColor")
            name_col.prop(s, "genos_exp_suf_emission", text="Emission")
            name_col.prop(s, "genos_exp_suf_normal", text="Normal")
            name_col.prop(s, "genos_exp_suf_roughness", text="Roughness")
            name_col.prop(s, "genos_exp_suf_metallic", text="Metallic")
            name_col.prop(s, "genos_exp_suf_opacity", text="Opacity")
            name_col.prop(s, "genos_exp_suf_ao", text="AO")
            name_col.prop(s, "genos_exp_suf_displacement", text="Displacement")
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
        box.label(text="• Normal Map: OpenGL/DirectX tangent normals with live strength and safe baking.", icon='DOT')
        box.label(text="• Displacement: Non-color height, bump in viewport + optional Cycles true displacement.", icon='DOT')
        box.label(text="• Roughness/Metallic/Opacity/AO: Standard data maps stay Non-Color and export without view transforms.", icon='DOT')

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

        box = layout.box()
        box.label(text="4. 3-Banded Cel Shading (Nikke / Wuwa / PGR)", icon='SHADING_RENDERED')
        box.label(text="• Band 1: Lit key to warm 1st midtone shadow transition.", icon='DOT')
        box.label(text="• Band 2: 1st shadow to 2nd deep core shadow occlusion.", icon='DOT')
        box.label(text="• SSS Fringe: Vibrant warm subsurface bleed along terminator edge.", icon='DOT')

        box = layout.box()
        box.label(text="5. Anime Hair Studio (Nikke / PGR)", icon='CURVES')
        box.label(text="• Angel Ring Halo: Bell-curve highlight band across hair crown.", icon='DOT')
        box.label(text="• Strands Breakup: Crisp anisotropic vertical streaks along hair locks.", icon='DOT')
        box.label(text="• Tip Ombre Gradient: Root-to-tip depth tint on hair ends.", icon='DOT')

        box = layout.box()
        box.label(text="6. Specular, Rim & Tech Special Effects", icon='LIGHT_SUN')
        box.label(text="• Dual Specular: Sharp anime glint core + soft sheen halo.", icon='DOT')
        box.label(text="• Directional Rim: Stylized backlight silhouette on mesh edges.", icon='DOT')
        box.label(text="• Tech Emission & Pulse: Vibrant sci-fi glow with breathing effect.", icon='DOT')

        box = layout.box()
        box.label(text="7. Anime FX Auto-Bake", icon='SHADING_TEXTURE')
        box.label(text="• Hair shaders: Bakes strand highlights to ILM Spec/Rim and Detail Accent.", icon='DOT')
        box.label(text="• Other shaders: Bakes eye sparkle + iris ring to ILM and Detail glow channels.", icon='DOT')

        box = layout.box()
        box.label(text="8. Mesh Selection & Multi-Object Isolation", icon='MOD_MASK')
        box.label(text="• Fork to Selection: In Edit Mode, creates an independent material slot for selected faces.", icon='DOT')
        box.label(text="• Direct Mask Fill: Burns glow, spec, or pattern values into selected faces' UV islands.", icon='DOT')
        box.label(text="• Make Unique: Decouples shared materials on duplicate objects so tweaks stay isolated.", icon='DOT')
        box.label(text="• Vertex FX (Anime_FX): Per-mesh attribute that overrides glow/spec/rim without touching material.", icon='DOT')

# -------------------------------------------------------------------
# Registration
# -------------------------------------------------------------------

classes = (
    GENOS_HairBandItem, GENOS_HairToonBandItem,
    GENOS_OT_convert_rgb_shadow_to_ilm,
    GENOS_OT_create_workspace, GENOS_OT_fix_render_settings, GENOS_OT_repair_textures, GENOS_OT_regenerate_shader, GENOS_OT_adapt_all_shaders,
    GENOS_OT_add_hair_band, GENOS_OT_remove_hair_band, GENOS_OT_duplicate_hair_band, GENOS_OT_move_hair_band,
    GENOS_OT_add_hair_toon_band, GENOS_OT_remove_hair_toon_band, GENOS_OT_duplicate_hair_toon_band, GENOS_OT_move_hair_toon_band,
    GENOS_OT_apply_hair_preset, GENOS_OT_apply_hair_preset_nikke, GENOS_OT_auto_harmonize_hair_color,
    GENOS_OT_detect_and_apply_hair_ombre, GENOS_OT_bake_hair_ombre,
    GENOS_OT_apply_cloth_preset, GENOS_OT_add_outline,
    GENOS_OT_set_material_shader_type, GENOS_OT_auto_classify_materials,
    GENOS_OT_fork_material_selection, GENOS_OT_make_material_unique, GENOS_OT_fill_selection_mask, GENOS_OT_set_vertex_fx_mask,
    GENOS_OT_bake_ao, GENOS_OT_bake_curvature, GENOS_OT_bake_specular, GENOS_OT_bake_sdf, GENOS_OT_bake_displacement, GENOS_OT_bake_normal, GENOS_OT_bake_standard_data_maps,
    GENOS_OT_bake_anime_fx,
    GENOS_OT_download_pattern_preset, GENOS_OT_download_all_pattern_presets, GENOS_OT_apply_cached_pattern_preset,
    GENOS_OT_set_paint_target, GENOS_OT_bake_preview_appearance,
    GENOS_OT_pack_ilm, GENOS_OT_pack_detail, GENOS_OT_save_all,
    GENOS_PT_workspace_panel, GENOS_PT_guide_panel
)

def register():
    # PropertyGroup must be registered before Scene CollectionProperty is created.
    # A failed previous enable can leave classes registered for the current Blender
    # session, so recover only from the specific "already registered" case.
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as exc:
            if 'already registered' not in str(exc).lower():
                raise
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
            bpy.utils.register_class(cls)
    register_scene_props()
    # bpy.data may still be _RestrictData here, so initialize scenes asynchronously.
    _register_dynamic_hair_band_deferred_init()

def unregister():
    # Stop callbacks/timers before deleting properties/classes.
    _unregister_dynamic_hair_band_deferred_init()
    # Remove CollectionProperty before unregistering its PropertyGroup type.
    unregister_scene_props()
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

if __name__ == "__main__":
    register()
