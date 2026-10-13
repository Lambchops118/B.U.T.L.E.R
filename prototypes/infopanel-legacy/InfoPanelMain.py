import sys
import pygame

def main():
    pygame.init()
    info_object = pygame.display.Info()
    screen_width, screen_height = info_object.current_w, info_object.current_h #get the screen width and height
    screen = pygame.display.set_mode((screen_width, screen_height), pygame.FULLSCREEN)
    pygame.display.set_caption("Scalable Shapes Example")

    # Clock to control frame rate if needed
    clock = pygame.time.Clock()

    # Define some colors (R, G, B)
    BLACK = (0, 0, 0)
    GREEN = (0, 255, 0)

    rect_width = int(screen_width * 0.2)  # 20% of screen width
    rect_height = int(screen_height * 0.1)  # 10% of screen height

    # For a circle, we can pick a radius that depends on a smaller dimension
    circle_radius = min(screen_width, screen_height) // 10  # 1/10 of smaller dimension

    # Prepare variables for shape positions
    # For instance, place the rectangle roughly in the center
    rect_x = (screen_width - rect_width) // 2
    rect_y = (screen_height - rect_height) // 2

    # Circle at some offset from center
    circle_x = screen_width // 4
    circle_y = screen_height // 4

    running = True
    while running:
        # Process events
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                # Escape key to quit full-screen
                if event.key == pygame.K_ESCAPE:
                    running = False

        screen.fill(BLACK)
        pygame.draw.rect(screen, GREEN, (rect_x, rect_y, rect_width, rect_height))
        pygame.draw.circle(screen, GREEN, (circle_x, circle_y), circle_radius)

        # Optionally draw more shapes here, all scaled similarly

        # Flip the display to update the contents of the window
        pygame.display.flip()
        # Limit the loop to 60 frames per second (optional)
        clock.tick(60)

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()