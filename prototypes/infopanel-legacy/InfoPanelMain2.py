import sys
import time
import pygame
import MBVectorArt
import gears


# Colors (R, G, B)
color = (0, 255, 0)            # self.color
color_offline = (100, 100, 100)  # self.colorOffline (example placeholder)
red = (255, 0, 0)

# -------------------------
# Placeholder gear function
# since original code used gears.gearPlace(...).
# You will need to implement real gear shapes if desired.
def gear_place(screen, degrees, color, center_x, center_y, scale_x, scale_y):
    gears.gear_place(screen, degrees, color, center_x, center_y)

# -------------------------
# Monkey Butler Vector Art
def draw_monkey_butler_head(screen, dx, dy, scale_x, scale_y, color):
    # Helper to scale points quickly
    def s(x, y):
        """Scale and return an (x, y) tuple."""
        return (int((dx + x) * scale_x), int((dy + y) * scale_y))
    MBVectorArt.draw_monkey_butler_head(screen, dx, dy)

def draw_scanlines(screen, screen_width, screen_height):
    for y in range(0, screen_width, 4):  # Adjust spacing for effect
        pygame.draw.line(screen, (0, 50, 0), (0, y), (8000, y), 1)


def static_drawings(screen, screen_width, screen_height, circle_time):

    # Example placeholders for text data from your original code
    time_readable = time.strftime("%H:%M:%S")  # self.timeReadable
    date_readable = time.strftime("%Y-%m-%d")  # self.dateReadable
    weekday = time.strftime("%A")              # self.weekday

    # Example booleans for "online" or "offline"
    is_discord_online = True
    is_server_online = False

    # For scaling from a “base resolution”
    base_w, base_h = 2560, 1440
    scale_x = screen_width / base_w
    scale_y = screen_height / base_h

    # Prepare a default font
    pygame_font = pygame.font.SysFont(None, int(30 * ((scale_x + scale_y) / 2)))

    # A helper to quickly draw scaled text
    def draw_text(text, x, y, color, size=30):
        # Create a font scaled to the user’s resolution
        font_scaled = pygame.font.Font(r"C:\Users\Liam\Desktop\Talos\Butler\InfoPanel\VT323-Regular.ttf", int(size * ((scale_x + scale_y) / 2)))
        surface = font_scaled.render(str(text), True, color)
        screen.blit(surface, (int(x * scale_x), int(y * scale_y)))

    def draw_text_centered(text, x, y, color, size=30):
        font_scaled = pygame.font.Font(r"C:\Users\Liam\Desktop\Talos\Butler\InfoPanel\VT323-Regular.ttf", 
                                    int(size * ((scale_x + scale_y) / 2)))
        surface = font_scaled.render(str(text), True, color)
        text_width = surface.get_width()
        text_height = surface.get_height()

        draw_x = int(x * scale_x - text_width / 2)
        draw_y = int(y * scale_y - text_height / 2)

        screen.blit(surface, (draw_x, draw_y))

    rect_x = int(screen_width/2 * scale_x)
    rect_y = int(screen_height/3.75 * scale_y)
    rect_w = int(415 * scale_x)
    rect_h = int(425 * scale_y)

    pygame.draw.rect(
        screen,
        color,
        pygame.Rect(rect_x - rect_w//2, rect_y - rect_h//2, rect_w, rect_h),
        width=5
    )

    #Title, time, etc
    draw_text_centered(time_readable, screen_width/2, screen_height/2.3, color, 35)
    draw_text_centered(date_readable, screen_width/2, screen_height/2.2, color, 35)
    draw_text_centered(weekday, screen_width/2, screen_height/2.1, color,  35)
    draw_text_centered("Monkey Butler", screen_width/2, screen_height/14, color, 80)

    #Gears and “online/offline” checks
    gear_color = color


    if is_server_online:
        degrees = circle_time * 2
        gear_place(screen, degrees, gear_color, 125, 125, scale_x, scale_y)
    else:
        degrees = 0
        gear_place(screen, degrees, color_offline, 125, 125, scale_x, scale_y)


    if is_discord_online:
        degrees = circle_time * 2
        gear_place(screen, degrees, gear_color, 350, 125, scale_x, scale_y)
    else:
        gear_place(screen, 0, color_offline, 350, 125, scale_x, scale_y)

    

   

def main():
    pygame.init()
    info = pygame.display.Info()
    screen_width, screen_height = info.current_w, info.current_h
    print(info.current_w, info.current_h)

    # Full-screen
    screen = pygame.display.set_mode((screen_width, screen_height), pygame.FULLSCREEN)
    pygame.display.set_caption("Scalable Pygame Port")

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
        draw_scanlines(screen, screen_width, screen_height)

        # Draw everything that used to be in Arcade
        static_drawings(screen, screen_width, screen_height, circle_time)


        #loop that animates butler's head
        second = int(time.strftime("%S"))
        if second % 2 == 0:
            dx, dy = 0, 10
        else:
            dx, dy = 0, 0

        # Draw Monkey Butler head at these offsets
        scale_x = 10
        scale_y = 10
        color = (0, 255, 0)
        draw_monkey_butler_head(screen, screen_width/3.2+dx, screen_height/2+dy, scale_x, scale_y, color)

        
        # Update the display
        pygame.display.flip()
        clock.tick(30)
        circle_time += 1  # increment for gear rotation tests

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()