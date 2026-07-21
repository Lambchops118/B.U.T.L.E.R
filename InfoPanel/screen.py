
import sys
import time
import queue
import pygame
import platform
from pathlib import Path
from   dotenv import load_dotenv; load_dotenv()

#import gears2 as gears
from . import butler_vector_art as MBVectorArt
from . import moving_vector_portrait as vec3d
from . import obj_wireframe_loader as objl
from . import screen_effects as fx
from . import windows
from .screen_effects import GpuCRT

font_path = str(Path(__file__).resolve().parent / "VT323-Regular.ttf")

# =============== PYGAME INFO PANEL ===============
color         = (0, 255, 100)
color_offline = (5, 5, 5)
red           = (255, 0, 0)

RESOLUTIONS = {
    "QHD"   : (2560, 1440),
    "UHD"   : (3840, 2160),
    "1080P" : (1920, 1080),
}

scale = 0.75
FORCED_WINDOW_SIZE = RESOLUTIONS["1080P"]


def choose_window_size(display_w, display_h):
    forced_w, forced_h = FORCED_WINDOW_SIZE
    window_w = min(forced_w, display_w)
    window_h = min(forced_h, display_h)
    return window_w, window_h

def parse_base_resolution():
    if len(sys.argv) < 2:
        return RESOLUTIONS["QHD"]
    arg = sys.argv[1].upper()
    if arg in RESOLUTIONS:
        return RESOLUTIONS[arg]
    else:
        print(f"Unknown resolution '{arg}'. Falling back to QHD.")
        return RESOLUTIONS["QHD"]

def draw_monkey_butler_head(screen, base_x, base_y, scale_x, scale_y, color_):
    MBVectorArt.draw_monkey_butler_head(screen, base_x, base_y, scale_x, scale_y, color_)

_font_cache = {}

def get_font(size, scale_x=1.0*scale, scale_y=1.0*scale):
    avg_scale = (scale_x + scale_y) / 2
    key = int(size * avg_scale)
    if key not in _font_cache:
         _font_cache[key] = pygame.font.Font(font_path, key)
    return _font_cache[key]


def platform_time_formats():
    if platform.system() == "Windows":
        return "%A %#I:%M %p", "%B %#d, %Y"
    return "%A %-I:%M %p", "%B %-d, %Y"


def static_drawings(screen, base_w, base_h, scale_x, scale_y, circle_time, scale):
    time_format, date_format = platform_time_formats()
    time_readable = time.strftime(time_format)
    date_readable = time.strftime(date_format)

    def draw_text_centered(text, bx, by, color, size=30):
        surf = get_font(size).render(str(text), True, color)
        rect = surf.get_rect(center=(bx * scale_x, by * scale_y))
        screen.blit(surf, rect)

    items = [
        (time_readable,   base_w/2,     base_h/2.3, 75),
        (date_readable,   base_w/2,     base_h/2.1, 75),
        ("Monkey Butler", base_w/2,     base_h/14,  80),
        ("Information",   base_w/4,     base_h/14,  50),
        ("Systems Status",base_w/1.25,  base_h/14,  50),
        ("Chopscorp. Ltd. c 1977", base_w-180, base_h-75, 30),
    ]

    for text, x, y, size in items:
        draw_text_centered(text, x, y, color, size)


def run_info_panel_gui(cmd_queue, scale): #The main Pygame loop. Polls 'cmd_queue' for new commands to display.
    print("Starting Pygame GUI for Info Panel...")

    pygame.init()
    info = pygame.display.Info()

    display_w, display_h = info.current_w, info.current_h
    screen_width, screen_height = choose_window_size(display_w, display_h)

    #w = screen_width
    #h = screen_height

    print("Detected screen resolution:", display_w, display_h)
    print("Forcing window size:", screen_width, screen_height)
    if (screen_width, screen_height) != FORCED_WINDOW_SIZE:
        print("Adjusted window size to fit the current display.")

    base_w, base_h = parse_base_resolution()
    print(f"Using base design resolution: {base_w}x{base_h}")

    mob_angle = 0

    screen = None
    crt = None
    try:
        crt = GpuCRT(window_size=(screen_width, screen_height),
               kx=0.18, ky=0.16, curv=0.3,
               scan=0.18, vign=0.45, gamma=2.0)
        print("GPU CRT effect enabled.")
    except Exception as exc:
        print(f"GPU CRT unavailable, falling back to standard Pygame rendering: {exc}")
        screen = pygame.display.set_mode((screen_width, screen_height))

    pygame.display.set_caption("Monkey Butler")

    scale_x = screen_width / base_w
    scale_y = screen_height / base_h

    clock        = pygame.time.Clock()
    running      = True
    circle_time  = 0
    angle        = 0

    # We'll keep track of the "last voice command" and "last GPT response"
    # so we can display them in the GUI.
    last_command  = "\"butler, water the monstera\""
    last_response = "of course, sir. i have activated the pump for the pot with the monstera."


    #========================================================================================
    # Off-screen render target
    framebuffer = pygame.Surface((screen_width, screen_height)).convert()
    framebuffer_alpha = pygame.Surface((screen_width, screen_height), pygame.SRCALPHA).convert_alpha()

    # Cached overlays (rebuild these if resolution changes)
    scanlines_surf = fx.build_scanlines(screen_width, screen_height, spacing=5, alpha=200)
    grille_surf    = fx.build_aperture_grille(screen_width, screen_height, pitch=3, alpha=18)
    vignette_surf  = fx.build_vignette(screen_width, screen_height, margin=24, edge_alpha=70, corner_radius=28)

    # Persistence buffer (previous post-processed frame)
    #last_frame = None
    #========================================================================================

    #Code for 3d wireframe panel
    panel_rect = (screen_width - 900 , 300, 340, 260) # x, y, w, h
    renderer   = vec3d.WireframeRenderer(panel_rect, fov=55, near=0.1, far=50) 
    mesh       = vec3d.cube_mesh(size=0.7) # Create a cube mesh
    angle      = 180.0 # Rotation angle for animation

    # Add a new dict with settings to add new dynamos to the screen.
    dynamo_system_status = {
        "mqtt": 1,
        "panels": 1,
        "waterer": 1,
        "discord": 0,
        "engassist": 1,
    }


    dynamo_configs = [
        dict(id="mqtt", x=1700, y=250, base_deg=45, surface=framebuffer, scale=scale, color=color, supertext= "MQTT Broker", subtext="[status]"),
        dict(id="panels",x=1700, y=475, base_deg=45, surface=framebuffer, scale=scale, color=color, supertext= "Home Automation MCP", subtext="[status]"),
        dict(id="waterer",x=1700, y=700, base_deg=45, surface=framebuffer, scale=scale, color=color, supertext= "Auto Waterer", subtext="[status]"),
        dict(id="discord",x=1700, y=925,  base_deg=45,  surface=framebuffer, scale=scale, color=color, supertext= "Discord Frontend", subtext=""),
        dict(id="engassist",x=1700, y=1150, base_deg=45, surface=framebuffer, scale=scale, color=color, supertext= "Engineering Assist", subtext="")
    ]
    
    # This takes the dynamo configs and creates the actual Dynamo objects.
    dynamos = [
        windows.Dynamo(
            windows.WidgetConfig(
                surface=framebuffer,
                x=cfg["x"],
                y=cfg["y"],
                obj_width=0,
                obj_height=0,
                scale=scale,
                color=(255, 0, 0),
                text="test text",
                line_width=5,
                font_size=(60)
            ),
            cfg["supertext"],
            cfg["subtext"],
            dynamo_system_status[cfg["id"]],
            cfg["base_deg"],
        )
        for cfg in dynamo_configs
    ]

    # Add new widget configs here to add new widgets to the screen.

    widget_statuses = {
        "btc_price": None,
        "eth_price": None,
        "sol_price": None,
        "temp"     : None,
        "feelslike": None,
        "humidity" : None,
        "wind"     : None,
        "wind_dir" : None,
        "weather"  : None,
        "uptime"     : None
    }

    widget_configs = [
        #Basic Information Box
        dict(x=145, y=170, obj_width=850, obj_height=500, surface=framebuffer, scale=scale, color=color,
             text=
             "BTC: ${btc_price}  ETH: ${eth_price} SOL: ${sol_price} \n" \
             "\n" \
             "Temperature: {temp}°F   Feels Like: {feelslike}°F\n" \
             "Humidity: {humidity}% \n" \
             "Wind: {wind}mph  {wind_direction}\n Weather: {weather}\n" \
             "\nUptime: {uptime} days | Net Worth: $101,749",
             fontsize = 55),
        
        #Voice Input Box
        dict(x=60, y=735, obj_width=1500, obj_height=150, surface=framebuffer, scale=scale, color=color,
             id="voice_cmd", 
             text="\"Butler, water the Monstera...\"", 
             fontsize = 60),

        #Voice Response Box
        dict(x=60, y=900, obj_width=1500, obj_height=450, surface=framebuffer, scale=scale, color=color, 
             id="voice_resp",
             text="Of course sir, the Monstera has been watered.", 
             fontsize = 60),
        
        #Portrait Box
        dict(x=1073, y=170, obj_width=415, obj_height=426, surface=framebuffer, scale=scale, color=color, text="", fontsize=0, line_width=8),
    ]

    # This takes the widget configs and creates the actual Widget objects.
    widgets = [
        windows.Widget(
            windows.WidgetConfig(
                surface=framebuffer,
                x=cfg["x"],
                y=cfg["y"],
                obj_width=cfg["obj_width"],
                obj_height=cfg["obj_height"],
                scale=scale,
                color=cfg["color"],
                text=cfg["text"],
                line_width=cfg.get("line_width", 5),
                font_size=cfg["fontsize"]
            )
        )
        for cfg in widget_configs
    ]


    #character = objl.load_obj_wire( "InfoPanel/butlerv3.obj", keep_edges="feature", # try "boundary" or "all" 
    #                                   feature_angle_deg=50.00, # larger -> fewer, sharper edges kept
    #                                     target_radius=0.8 )

    while running: # [][]][][][][][][][][][][][][][][][][]MAIN LOOP[][][][][][][][][][][][][][][][][]
        # --- EVENT HANDLING ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

        # --- POLL THE QUEUE ---
        # Collect all commands currently in the queue
        while True:
            try:
                msg = cmd_queue.get_nowait()
            except queue.Empty:
                break
            else:
                if msg[0] == "VOICE_CMD":
                    # msg structure: ("VOICE_CMD", recognized_command, gpt_response_text)
                    last_command  = msg[1]
                    last_response = msg[2]
                elif msg[0] == "STATUS":
                    # msg structure: ("STATUS", {"key": value, ...})
                    if isinstance(msg[1], dict):
                        widget_statuses.update(msg[1])

        # --- RENDER THE FRAME --- 
        framebuffer.fill((0, 1, 0)) 
        static_drawings(framebuffer, base_w, base_h, scale_x, scale_y, circle_time, scale)
        second = int(time.strftime("%S"))
        dy = 10 if second % 2 == 0 else 0
        mb_base_x = base_w / 3.2
        mb_base_y = base_h / 2 + dy

        # Update Dynamo Statuses Here.
        dynamo_system_status["mqtt"]         = 1 #if (second >= 0) else 0  #placeholder code to simulate status changes. replace with actual
        dynamo_system_status["panels"]       = 0 #if (second >= 10) else 0
        dynamo_system_status["waterer"]      = 0 #if (second >= 20) else 0
        dynamo_system_status["discord"]      = 1 #if (second >= 30) else 0
        dynamo_system_status["engassist"]    = 0 #if (second >= 40) else 0

        debug = False
        if debug:
            draw_monkey_butler_head(framebuffer, mb_base_x+200, mb_base_y+150, scale_x, scale_y, color)
        else:
            draw_monkey_butler_head(framebuffer, mb_base_x, mb_base_y, scale_x, scale_y, color)

        #draw_text_topleft(f"Last command:  {last_command}",  75, 740, color, 36, target=framebuffer)
        #draw_text_topleft(f"Last response: {last_response}", 75, 900, color, 36, target=framebuffer)

        # Draw Widgets to the screen
        for d, cfg in zip(dynamos, dynamo_configs):
            d.system_status = dynamo_system_status[cfg["id"]]
            d.subtext = "online" if d.system_status == 1 else "offline"
            if d.system_status == 1:
                d.degrees = (d.degrees + 4) % 360
            d.draw_dynamo()

        for w, cfg in zip(widgets, widget_configs):
                w.drawCenteredRect()
                if cfg.get("id") == "voice_cmd":
                    w.createTextArea(last_command)
                elif cfg.get("id") == "voice_resp":
                    w.createTextArea(last_response)
                else:
                    safe = {k: ("--" if v is None else v) for k, v in widget_statuses.items()}
                    safe["wind_direction"] = safe.get("wind_dir", "--")
                    w.createTextArea(cfg["text"].format(**safe))


        #renderer.draw(
        #    framebuffer,
        #    character,
        #    model_pos     = (0.0, -0.1, 3.2),
        #    model_rot     = (0, mob_angle*0.9, 0),
        #    model_scale   = 3.5,
        #    camera_pos    = (0, 0, 0),
        #    camera_target = (0, 0, 1),
        #    zsort         = True
        #)
        # def draw_mouse_coordinates(surface):
        #     x, y = pygame.mouse.get_pos()
        #     text = font_scaled.render(f"({x}, {y})", True, (255, 255, 255))
        #     surface.blit(text, (10, 10))  # Display in top-left corner
        # draw_mouse_coordinates(framebuffer)

        # === POST FX on a copy (so we can reuse framebuffer if needed) ===
        post = framebuffer#.copy()
        #last_frame = post
        fx.add_bloom(post, strength=1, down=0.45)
        #post = fx.apply_persistence(last_frame, post, alpha=80)
        post.blit(grille_surf,   (0, 0))
        post.blit(vignette_surf, (0, 0))
        post.blit(scanlines_surf,(0, 0))
        if crt is not None:
            crt.draw_surface(post)
        else:
            screen.blit(post, (0, fx.random_vertical_jitter_y(100)))
            pygame.display.flip()
        #last_frame = post

        clock.tick(60)
        circle_time += 1
        mob_angle += 0.01

    pygame.quit()
    sys.exit()
