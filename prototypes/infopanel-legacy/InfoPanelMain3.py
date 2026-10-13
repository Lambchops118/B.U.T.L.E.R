import sys
import time
import pygame
import InfoPanel.butler_vector_art as MBVectorArt
import InfoPanel.gears as gears

# --------------------------
# Colors (R, G, B)
color = (0, 255, 0)         # "online" color
color_offline = (100, 100, 100)  # "offline" color
red = (255, 0, 0)

# --------------------------
# Resolution configurations
# (You can add or remove as you wish)
RESOLUTIONS = {
    "QHD": (2560, 1440),
    "UHD": (3840, 2160),
    "1080P": (1920, 1080),
}

def parse_base_resolution():
    """
    Checks sys.argv for a resolution specifier.
    If none is given, defaults to QHD (2560×1440).
    """
    if len(sys.argv) < 2:
        return RESOLUTIONS["QHD"]  # default if not specified

    arg = sys.argv[1].upper()
    if arg in RESOLUTIONS:
        return RESOLUTIONS[arg]
    else:
        print(f"Unknown resolution '{arg}'. Falling back to QHD.")
        return RESOLUTIONS["QHD"]

# --------------------------
# A gear helper that expects base coords, then does the scaling internally
#def gear_place(screen, degrees, color, base_x, base_y, scale_x, scale_y):
 #   """
  #  Draws a gear at base coords (base_x, base_y), then scales
   # to the current screen resolution before calling gears.gear_place.
    #"""
    #scaled_x = int(base_x * scale_x)
    #scaled_y = int(base_y * scale_y)
    #gears.gear_place(screen, degrees, color, scaled_x, scaled_y)
def gear_place(screen, degrees, color, center_x, center_y, scale_x, scale_y):
    # Convert base coords to real screen coords
    scaled_x = int(center_x * scale_x)
    scaled_y = int(center_y * scale_y)
    gears.gear_place(screen, degrees, color, scaled_x, scaled_y, scale_x, scale_y)

# --------------------------
# Monkey Butler Vector Art, again expecting base coords
def draw_monkey_butler_head(screen, base_x, base_y, scale_x, scale_y, color):
    """
    Draws the monkey butler head at (base_x, base_y) in base coords.
    We scale them before passing to MBVectorArt.
    """
    scaled_x = int(base_x * scale_x)
    scaled_y = int(base_y * scale_y)
    #MBVectorArt.draw_monkey_butler_head(screen, scaled_x, scaled_y)
    MBVectorArt.draw_monkey_butler_head(screen, base_x, base_y, scale_x, scale_y, (0, 255, 0))

# --------------------------
def draw_scanlines(screen, screen_width, screen_height):
    # This can stay as-is since it's purely using the actual screen size
    for y in range(0, screen_width, 4):  # Adjust spacing for effect
        pygame.draw.line(screen, (0, 50, 0), (0, y), (8000, y), 1)

# --------------------------
def static_drawings(screen, base_w, base_h, scale_x, scale_y, circle_time):
    """
    Performs “static” drawings (rectangle, text, gears, etc.).
    Expects scale_x and scale_y from the main function, plus the base resolution.
    """

    # Example placeholders for time/date
    time_readable = time.strftime("%H:%M:%S")
    date_readable = time.strftime("%Y-%m-%d")
    weekday = time.strftime("%A")

    # Example booleans for "online" or "offline"
    is_discord_online = True
    is_server_online = False

    # Adjust font path for your environment as needed
    font_path = r"C:\Users\Liam\Desktop\Talos\Butler\InfoPanel\VT323-Regular.ttf"

    def draw_text(text, base_x, base_y, color, size=30):
        """
        Draw text at (base_x, base_y) in base coords, scaled up to the real screen.
        """
        font_scaled = pygame.font.Font(
            font_path,
            int(size * ((scale_x + scale_y) / 2))
        )
        surface = font_scaled.render(str(text), True, color)
        screen.blit(
            surface,
            (int(base_x * scale_x), int(base_y * scale_y))
        )

    def draw_text_centered(text, base_x, base_y, color, size=30):
        """
        Draw text centered around (base_x, base_y) in base coords,
        scaled up to the real screen.
        """
        font_scaled = pygame.font.Font(
            font_path,
            int(size * ((scale_x + scale_y) / 2))
        )
        surface = font_scaled.render(str(text), True, color)
        text_width = surface.get_width()
        text_height = surface.get_height()

        draw_x = int(base_x * scale_x - text_width / 2)
        draw_y = int(base_y * scale_y - text_height / 2)
        screen.blit(surface, (draw_x, draw_y))

    # --------------------------------------------------
    # Draw a rectangle in the center. We define it in base coords
    # and then scale when we draw.
    rect_base_x = base_w / 2
    rect_base_y = base_h / 3.75
    rect_base_w = 415
    rect_base_h = 425

    scaled_rect_x = int(rect_base_x * scale_x - (rect_base_w * scale_x) / 2)
    scaled_rect_y = int(rect_base_y * scale_y - (rect_base_h * scale_y) / 2)
    scaled_rect_w = int(rect_base_w * scale_x)
    scaled_rect_h = int(rect_base_h * scale_y)

    pygame.draw.rect(
        screen,
        color,
        pygame.Rect(scaled_rect_x, scaled_rect_y, scaled_rect_w, scaled_rect_h),
        width=5
    )



    # --------------------------------------------------
    # Title, time, date, etc. Use base coords for their positions
    draw_text_centered(time_readable, base_w / 2, base_h / 2.3, color, 35)
    draw_text_centered(date_readable, base_w / 2, base_h / 2.2, color, 35)
    draw_text_centered(weekday,      base_w / 2, base_h / 2.1, color, 35)
    draw_text_centered("Monkey Butler", base_w / 2, base_h / 14, color, 80)

    # --------------------------------------------------
    # Gears in the top-left area (base coords)
    # We'll position them at (125,125) and (350,125) in base coords
    if is_server_online:
        degrees = circle_time * 2
        gear_place(screen, degrees, color, 125, 125, scale_x, scale_y)
    else:
        gear_place(screen, 0, color_offline, 125, 125, scale_x, scale_y)

    if is_discord_online:
        degrees = circle_time * 2
        gear_place(screen, degrees, color, 350, 125, scale_x, scale_y)
    else:
        gear_place(screen, 0, color_offline, 350, 125, scale_x, scale_y)

# --------------------------
def main():
    pygame.init()
    info = pygame.display.Info()

    # Actual screen resolution (the real size of your display)
    screen_width, screen_height = info.current_w, info.current_h
    print("Detected screen resolution:", screen_width, screen_height)

    # Decide on the "design resolution" (QHD, UHD, etc.)
    base_w, base_h = parse_base_resolution()
    print(f"Using base design resolution: {base_w}x{base_h}")

    # Start fullscreen at the real display size
    screen = pygame.display.set_mode((screen_width, screen_height), pygame.FULLSCREEN)
    pygame.display.set_caption("Scalable Pygame Port")

    # Compute the scale factors relative to the chosen “design resolution”
    scale_x = screen_width / base_w
    scale_y = screen_height / base_h

    clock = pygame.time.Clock()
    running = True
    circle_time = 0  # used for gear rotations, etc.

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

        # Clear screen
        screen.fill((0, 0, 0))

        # Optional scanline effect, uses actual screen coords
        draw_scanlines(screen, screen_width, screen_height)

        # Draw everything that used to be in "Arcade"
        static_drawings(screen, base_w, base_h, scale_x, scale_y, circle_time)

        # -----------------------------------
        # Animate the Monkey Butler head. Decide on a base offset in "design coordinates".
        second = int(time.strftime("%S"))
        # We'll just do a +10 shift in base coords if even seconds
        if second % 2 == 0:
            dy = 10
        else:
            dy = 0

        # Put him around (base_w/3.2, base_h/2) in base coords
        # Then add that small offset for an up/down effect
        mb_base_x = base_w / 3.2
        mb_base_y = base_h / 2 + dy

        # Draw the monkey butler head with the same scale factors
        draw_monkey_butler_head(
            screen,
            mb_base_x,
            mb_base_y,
            scale_x,
            scale_y,
            (0, 255, 0)
        )

        # Update the display
        pygame.display.flip()
        clock.tick(30)
        circle_time += 1  # increment gear rotation

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()