
import math
import random
import numpy as np
import pygame, moderngl
from pygame.locals import DOUBLEBUF, OPENGL

# ===== shaders =====

VS_SRC = """
#version 330
in vec2 in_pos;              // clip-space quad: [-1,1]
out vec2 v_uv;               // 0..1 UV
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    v_uv = in_pos * 0.5 + 0.5;
}
"""

FS_SRC = """
#version 330
uniform sampler2D u_tex;
uniform vec2      u_texSize; // (w, h)
uniform float     u_kx;      // barrel strength x
uniform float     u_ky;      // barrel strength y
uniform float     u_curv;    // overall curvature scale
uniform float     u_scan;    // scanline strength (0..1)
uniform float     u_vign;    // vignette strength (0..1)
uniform float     u_gamma;   // display gamma
uniform float u_zoom;   // 1.00 = no zoom. 1.05..1.20 = small overscan
uniform float     u_dim;     // 1.0 = normal, <1 = sleep-mode dim



in vec2  v_uv;
out vec4 fragColor;

void main() {
    // center coords around 0,0 and correct for aspect
    vec2 uv      = v_uv;
    vec2 center  = vec2(0.5);
    vec2 p       = uv - center;
    float aspect = u_texSize.x / u_texSize.y;
    p.x         *= aspect;

    
    // barrel warp: r' = r * (1 + k*r^2)
    float r2 = dot(p, p);
    float kx = u_kx * u_curv;
    float ky = u_ky * u_curv;
    vec2 k   = vec2(kx, ky);
    vec2 pw  = p * (1.0 + k * r2);   // per-axis strength
    pw.x    /= aspect;

    vec2 warped = center + pw;
    // after computing `warped`:
    warped = (warped - vec2(0.5)) / u_zoom + vec2(0.5);

    // clamp or discard outside
    if (any(lessThan(warped, vec2(0.0))) || any(greaterThan(warped, vec2(1.0)))) {
        fragColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    // sample
    vec3 col = texture(u_tex, warped).rgb;

    // simple scanlines
    float scan = mix(1.0, 0.75, u_scan);
    float line = mix(1.0, 0.5, step(0.5, fract(gl_FragCoord.y * 0.5)));
    col       *= mix(1.0, line, u_scan);

    // vignette
    float v = 1.0 - smoothstep(0.6, 0.95, sqrt(r2));
    col    *= mix(1.0, v, u_vign);

    // gamma
    col       = pow(col, vec3(1.0 / max(u_gamma, 0.001)));

    // Sleep-mode dim, applied LAST -- after gamma, deliberately.
    //
    // Dimming the frame on the CPU before this shader did not work: gamma here
    // is a 1/2.0 power, so a 1% multiply came back out as sqrt(0.01) = 10% on
    // the glass, and the 8-bit multiply had already crushed every mid-tone to
    // 0 or 1 on the way in. Scaling the final linear output instead means the
    // requested level is the level you actually see, with the picture's tonal
    // range intact.
    col      *= clamp(u_dim, 0.0, 1.0);
    fragColor = vec4(col, 1.0);
}
"""

W, H           = 1920, 1080 # window size
GAME_W, GAME_H = 1920, 1080 # internal render size (keeps warp fast)

#W, H           = 2560, 1440 # window size
#GAME_W, GAME_H = 2560, 1440 # internal render size (keeps warp fast)

def build_scanlines(w, h, spacing=2, alpha=100):
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    line = pygame.Surface((w, 2), pygame.SRCALPHA)
    line.fill((0, 0, 0, alpha))
    for y in range(0, h, spacing):
        surf.blit(line, (0, y))
    return surf.convert_alpha()

def build_aperture_grille(w, h, pitch=3, alpha=18):
    # Vertical dark lines to mimic Trinitron/aperture grille
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    col  = pygame.Surface((1, h), pygame.SRCALPHA)
    col.fill((0, 0, 0, alpha))
    for x in range(0, w, pitch):
        surf.blit(col, (x, 0))
    return surf.convert_alpha()

def build_vignette(w, h, margin=24, edge_alpha=70, corner_radius=28):
    # Cheap vignette via repeated translucent rect strokes (no per-pixel loops)
    surf  = pygame.Surface((w, h), pygame.SRCALPHA)
    steps = 6
    for i in range(steps):
        a   = int(edge_alpha * (i + 1) / steps)
        pad = margin * (steps - i) // steps
        pygame.draw.rect(
            surf,
            (0, 0, 0, a),
            pygame.Rect(pad, pad, w - 2 * pad, h - 2 * pad),
            width=corner_radius // 2,
            border_radius=corner_radius
        )
    return surf.convert_alpha()

def add_bloom(base, strength=0.65, down=0.25):
    w, h    = base.get_size()
    small   = pygame.transform.smoothscale(base, (max(1, int(w * down)), max(1, int(h * down))))
    blurred = pygame.transform.smoothscale(small, (w, h))
    blurred.set_alpha(int(255 * strength))
    # Additive blend where supported; fallback: regular alpha
    #try:
    #    base.blit(blurred, (0, 0), special_flags=pygame.BLEND_ADD)
    #except:
    #    base.blit(blurred, (0, 0))

    base.blit(blurred, (0, 0), special_flags=pygame.BLEND_ADD)

def barrel_warp_strips(src, k=0.08, strips=120):
    """Barrel warp via vertical strips. k ~0.06-0.12 looks nice."""
    w, h    = src.get_size()
    dst     = pygame.Surface((w, h), pygame.SRCALPHA).convert_alpha()
    strip_w = max(1, w // strips)
    cx      = w / 2.0

    for i in range(strips):
        x0  = i * strip_w
        x1  = w if i == strips - 1 else (i + 1) * strip_w
        sub = src.subsurface((x0, 0, x1 - x0, h))
        mid = (x0 + x1) * 0.5
        xn  = (mid - cx) / cx
        dx  = int(k * (xn * abs(xn)) * cx)  # outward at edges
        dst.blit(sub, (x0 + dx, 0))
    return dst

def apply_persistence(persist_surf, current, alpha=90):
    """EMA-style ghosting: draw last frame faintly behind current."""
    if persist_surf is None:
        return current.copy()
    ghost = persist_surf.copy()
    ghost.set_alpha(alpha)  # smaller = longer trails
    out   = current.copy()
    out.blit(ghost, (0, 0))
    return out

def apply_flicker(target, t, max_dark=18):
    """60Hz-ish ripple + random wobble. Very cheap."""
    # Small sine for regular ripple, a bit of random to break uniformity
    phase = (pygame.time.get_ticks() / 10000.0) * 59.94
    a     = int(max_dark * (0.5 + 0.5 * math.sin(phase * 2 * math.pi)) + random.uniform(-2, 2))
    if a <= 0: 
        return
    overlay = pygame.Surface(target.get_size(), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, max(0, min(255, a))))
    target.blit(overlay, (0, 0))

def random_vertical_jitter_y(max_px=90):
    tick = random.randint(1, 60)
    if tick == 30:
        return random.randint(-max_px, max_px)
    else:
        return 0

#OLD BARREL DISTORTION -- USES CPU PRECOMPUTE + NN SAMPLING==================================

def precompute_map(w, h, kx=0.12, ky=0.12):
    # normalized pixel centers in [-1, 1]
    xs     = np.linspace(-1.0, 1.0, w, dtype=np.float32)
    ys     = np.linspace(-1.0, 1.0, h, dtype=np.float32)
    Xd, Yd = np.meshgrid(xs, ys)         # shape (h, w)

    # elliptical radius so we can control x/y curvature separately
    r2 = -(Xd**2) + -(Yd**2)
    # inverse barrel: Xs = Xd / (1 + kx*r2), Ys = Yd / (1 + ky*r2)
    # (This approximates the true inverse; good for small k.)
    Xs = Xd / (1.0 + kx * r2* 0.25)  #the 0.25 controls how curvy the distortion is
    Ys = Yd / (1.0 + ky * r2* 0.25)  #same

    # map back to source pixel indices
    src_x = (Xs * 0.5 + 0.5) * (w - 1)
    src_y = (Ys * 0.5 + 0.5) * (h - 1)

    # integer indices for nearest-neighbor sampling
    ix = np.clip(np.rint(src_x), 0, w - 1).astype(np.int32)
    iy = np.clip(np.rint(src_y), 0, h - 1).astype(np.int32)
    return ix, iy, Xd, Yd

def make_crt_masks(w, h, strength_scan=0.18, strength_vignette=0.35): #0.18, 0.35
    # scanlines: darken every other row a bit
    scan = np.ones((h, w, 1), dtype=np.float32)
    scan[1::2, :, 0] = 1.0 - strength_scan

    # vignette: darken corners radially (smooth)
    xs   = np.linspace(-1.0, 1.0, w, dtype=np.float32)
    ys   = np.linspace(-1.0, 1.0, h, dtype=np.float32)
    X, Y = np.meshgrid(xs, ys)           # (h, w)
    r    = np.sqrt(X*X + Y*Y)
    vign = 1.0 - strength_vignette * (r**2)  # gentle
    vign = np.clip(vign, 0.0, 1.0).reshape(h, w, 1)

    return scan, vign

#SCAN, VIGN = make_crt_masks(GAME_W, GAME_H)
#IX, IY, XNORM, YNORM = precompute_map(GAME_W, GAME_H, kx=0.12, ky=0.10)
def warp_crt(src_surf):
    """
    src_surf must be GAME_W x GAME_H.
    Returns a warped pygame.Surface at the same size (then we scale to window).
    """
    # Pygame’s array3d is (w, h, 3). We’ll transpose to (h, w, 3) for easy indexing.
    src = pygame.surfarray.array3d(src_surf).astype(np.uint8)          # (w, h, 3)
    src = np.transpose(src, (1, 0, 2))                                  # (h, w, 3)

    # nearest-neighbor sampling using the inverse map
    warped = src[IY, IX]                                                # (h, w, 3)

    # apply scanlines & vignette (broadcast multiply)
    warped_lin = warped.astype(np.float32) / 255.0
    warped_lin *= SCAN
    warped_lin *= VIGN

    # slight overall gamma to mimic phosphor response
    gamma = 2.0
    warped_lin = np.clip(warped_lin, 0.0, 1.0) ** (1.0 / gamma)

    # back to surface
    warped_u8 = (warped_lin * 255.0 + 0.5).astype(np.uint8)
    warped_u8 = np.transpose(warped_u8, (1, 0, 2))                      # (w, h, 3)
    return pygame.surfarray.make_surface(warped_u8)



#game_surf = pygame.Surface((GAME_W, GAME_H))

# ================GPU BARREL WARP ===========================================
class GpuCRT:
    def __init__(self, window_size=(GAME_W, GAME_H),
                 kx=1, ky=1, scan=1, vign=10, gamma=20.0, curv=1):
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(
            pygame.GL_CONTEXT_PROFILE_MASK,
            pygame.GL_CONTEXT_PROFILE_CORE,
        )
        # Pygame GL window
        pygame.display.set_mode(window_size, DOUBLEBUF | OPENGL)
        self.w, self.h = window_size

        # ModernGL context
        self.ctx = moderngl.create_context(require=330)
        self.ctx.enable(moderngl.BLEND)

        # Program
        self.prog = self.ctx.program(vertex_shader=VS_SRC, fragment_shader=FS_SRC)

        # Fullscreen quad (-1..1)
        quad = np.array([
            -1.0, -1.0,
             1.0, -1.0,
            -1.0,  1.0,
            -1.0,  1.0,
             1.0, -1.0,
             1.0,  1.0,
        ], dtype='f4')
        self.vbo = self.ctx.buffer(quad.tobytes())
        self.vao = self.ctx.simple_vertex_array(self.prog, self.vbo, 'in_pos')

        # Source texture placeholder (created on first draw)
        self.tex = None

        # Uniforms (constant unless you change them)
        self.prog['u_kx'].value = kx
        self.prog['u_ky'].value = ky
        self.prog['u_curv'].value = curv
        self.prog['u_scan'].value = scan
        self.prog['u_vign'].value = vign
        self.prog['u_gamma'].value = gamma
        self.prog['u_texSize'].value = window_size
        self.prog['u_zoom'].value = 1.05  # try 1.08–1.18 depending on warp strength
        self.prog['u_dim'].value = 1.0

    def _ensure_texture(self, size):
        if self.tex is None or self.tex.size != size:
            # RGBA8 texture with linear filtering
            self.ctx.viewport = (0, 0, self.w, self.h)
            self.tex = self.ctx.texture(size, 3, dtype='f1')  # 3 = RGB
            self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.tex.repeat_x = False
            self.tex.repeat_y = False
            self.prog['u_texSize'].value = size
            self.prog['u_tex'].value = 0  # bound to tex unit 0
            self.tex.use(location=0)

    def set_dim(self, level):
        """Set output brightness, 1.0 = normal. Cheap enough to call per frame.

        Sleep mode drives this; the uniform is only re-uploaded when the value
        actually changes, so a fully lit panel costs nothing.
        """
        level = min(max(float(level), 0.0), 1.0)
        if level != self.prog['u_dim'].value:
            self.prog['u_dim'].value = level

    def draw_surface(self, src_surf):
        """Upload src_surf and draw warped to the GL backbuffer."""
        # Pull pixels as RGB bytes; this is fast enough if the surface is display-format
        size = src_surf.get_size()
        raw = pygame.image.tostring(src_surf, 'RGB', True)
        self._ensure_texture(size)
        self.tex.use(location=0)
        self.tex.write(raw)

        # Draw
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)
        self.vao.render()  # draws full-screen quad
        pygame.display.flip()



#Test code to actually run the GPU CRT warp (not used in main app)

# def main():
#     pygame.init()

#     # Set OpenGL-enabled display first
#     pygame.display.set_mode((GAME_W, GAME_H), DOUBLEBUF | OPENGL)
#     clock = pygame.time.Clock()

#     # Now it's safe to create and convert Surfaces
#     logical = pygame.Surface((GAME_W, GAME_H)).convert()

#     # Initialize the GPU CRT effect AFTER the display is created
#     crt = GpuCRT(window_size=(GAME_W, GAME_H),
#                  #kx=0.12, ky=0.10, scan=0.18, vign=0.35, gamma=2.0, curv=0.25)
#                   kx=0.12, ky=0.12, scan=1, vign=0.25, gamma=1, curv=0.35)

#     running = True
#     t = 0.0
#     while running:
#         for e in pygame.event.get():
#             if e.type == pygame.QUIT:
#                 running = False

#         # Draw to logical surface
#         logical.fill((20, 30, 40))
#         x = int((np.sin(t) * 0.5 + 0.5) * (GAME_W - 100))
#         #pygame.draw.rect(logical, (240, 180, 60), (x, GAME_H // 80, 100, 200))
#         pygame.draw.rect(logical, (240, 180, 60), (x, 50, 100, 200))
#         t += 0.02

#         # Draw warped version via GPU
#         crt.draw_surface(logical)

#         clock.tick(60)

#     pygame.quit()

# if __name__ == "__main__":
#     main()
