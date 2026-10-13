
import pygame

def draw_monkey_butler_head(screen, dx, dy):
    """
    Draws the Monkey Butler vector art portrait at (dx, dy) in PyGame,
    correcting Arcade's bottom-left origin to PyGame's top-left origin.
    """

    # Arcade color equivalents
    color_green = (0, 255, 0)
    color_black = (0, 0, 0)

    # Get the screen height so we can invert Y coordinates
    screen_height = screen.get_height()

    # A helper function to transform Arcade coords -> PyGame coords
    # Arcade: (0,0) at bottom-left, PyGame: (0,0) at top-left
    def t(ax, ay):
        """
        Transforms the Arcade coordinate (ax, ay) to the PyGame coordinate system, 
        offset by (dx, dy) but flipped vertically so it isn't upside down.
        """
        return (dx + ax, screen_height - (dy + ay))

    c = color_green  # For brevity

    #
    # NOSE
    #
   # pygame.draw.circle(screen, c, t(465, 325), 1)
    pygame.draw.line(screen, c, t(465, 325), t(470, 310), width=1)
    #pygame.draw.circle(screen, c, t(495, 325), 1)

    #pygame.draw.circle(screen, c, t(470, 310), 1)
    pygame.draw.line(screen, c, t(470, 310), t(465, 270), width=1)
    #pygame.draw.circle(screen, c, t(490, 310), 1)
    pygame.draw.line(screen, c, t(490, 310), t(495, 325), width=1)

    #pygame.draw.circle(screen, c, t(475, 280), 1)
    pygame.draw.line(screen, c, t(475, 280), t(480, 270), width=1)
    #pygame.draw.circle(screen, c, t(485, 280), 1)
    pygame.draw.line(screen, c, t(485, 280), t(495, 270), width=1)

    #pygame.draw.circle(screen, c, t(465, 270), 1)
    pygame.draw.line(screen, c, t(465, 270), t(475, 280), width=1)
    #pygame.draw.circle(screen, c, t(480, 270), 1)
    pygame.draw.line(screen, c, t(480, 270), t(485, 280), width=1)
    #pygame.draw.circle(screen, c, t(495, 270), 1)
    pygame.draw.line(screen, c, t(495, 270), t(490, 310), width=1)

    #
    # HEAD
    #
    #pygame.draw.circle(screen, c, t(365, 340), 1)
    pygame.draw.line(screen, c, t(365, 340), t(365, 305), width=1)
    #pygame.draw.circle(screen, c, t(595, 340), 1)

    #pygame.draw.circle(screen, c, t(365, 305), 1)
    pygame.draw.line(screen, c, t(365, 305), t(380, 300), width=1)
    #pygame.draw.circle(screen, c, t(595, 305), 1)
    pygame.draw.line(screen, c, t(595, 305), t(595, 340), width=1)

    #pygame.draw.circle(screen, c, t(380, 300), 1)
    pygame.draw.line(screen, c, t(380, 300), t(380, 240), width=1)
    #pygame.draw.circle(screen, c, t(580, 300), 1)
    pygame.draw.line(screen, c, t(580, 300), t(595, 305), width=1)

    #pygame.draw.circle(screen, c, t(380, 240), 1)
    pygame.draw.line(screen, c, t(380, 240), t(430, 215), width=1)
    #pygame.draw.circle(screen, c, t(580, 240), 1)
    pygame.draw.line(screen, c, t(580, 240), t(580, 300), width=1)

    #pygame.draw.circle(screen, c, t(430, 215), 1)
    pygame.draw.line(screen, c, t(430, 215), t(430, 200), width=1)
    #pygame.draw.circle(screen, c, t(530, 215), 1)
    pygame.draw.line(screen, c, t(530, 215), t(580, 240), width=1)

    #pygame.draw.circle(screen, c, t(430, 200), 1)
    pygame.draw.line(screen, c, t(430, 200), t(480, 190), width=1)
    #pygame.draw.circle(screen, c, t(530, 200), 1)
    pygame.draw.line(screen, c, t(530, 200), t(530, 215), width=1)

    #pygame.draw.circle(screen, c, t(480, 190), 1)
    pygame.draw.line(screen, c, t(480, 190), t(530, 200), width=1)

    #
    # SNOUT (brow)
    #
    #pygame.draw.circle(screen, c, t(440, 385), 1)
    pygame.draw.line(screen, c, t(440, 385), t(460, 380), width=1)
    #pygame.draw.circle(screen, c, t(460, 380), 1)
    pygame.draw.line(screen, c, t(460, 380), t(480, 375), width=1)
    #pygame.draw.circle(screen, c, t(480, 375), 1)
    pygame.draw.line(screen, c, t(480, 375), t(500, 380), width=1)
    #pygame.draw.circle(screen, c, t(500, 380), 1)
    pygame.draw.line(screen, c, t(500, 380), t(520, 385), width=1)
    #pygame.draw.circle(screen, c, t(520, 385), 1)

    # muzzle and mouth
    #pygame.draw.circle(screen, c, t(455, 310), 1)
    pygame.draw.line(screen, c, t(455, 310), t(435, 265), width=1)
    #pygame.draw.circle(screen, c, t(505, 310), 1)

    #pygame.draw.circle(screen, c, t(435, 265), 1)
    pygame.draw.line(screen, c, t(435, 265), t(435, 250), width=1)
    #pygame.draw.circle(screen, c, t(525, 265), 1)
    pygame.draw.line(screen, c, t(525, 265), t(505, 310), width=1)

    #pygame.draw.circle(screen, c, t(435, 250), 1)
    pygame.draw.line(screen, c, t(435, 250), t(455, 225), width=1)
    #pygame.draw.circle(screen, c, t(525, 250), 1)
    pygame.draw.line(screen, c, t(525, 250), t(525, 265), width=1)
    pygame.draw.line(screen, c, t(525, 250), t(495, 255), width=1)

    #pygame.draw.circle(screen, c, t(455, 225), 1)
    pygame.draw.line(screen, c, t(455, 225), t(480, 220), width=1)
    #pygame.draw.circle(screen, c, t(505, 225), 1)
    pygame.draw.line(screen, c, t(505, 225), t(480, 220), width=1)
    #pygame.draw.circle(screen, c, t(480, 220), 1)
    pygame.draw.line(screen, c, t(505, 225), t(525, 250), width=1)

    # mouth
    #pygame.draw.circle(screen, c, t(465, 255), 1)
    #pygame.draw.circle(screen, c, t(495, 255), 1)
    pygame.draw.line(screen, c, t(465, 255), t(435, 250), width=1)
    pygame.draw.line(screen, c, t(465, 255), t(495, 255), width=1)

    #
    # HAIR
    #
    #pygame.draw.circle(screen, c, t(480, 425), 1)
    pygame.draw.line(screen, c, t(480, 425), t(460, 435), width=1)
    #pygame.draw.circle(screen, c, t(460, 435), 1)
    pygame.draw.line(screen, c, t(460, 435), t(400, 415), width=1)
    #pygame.draw.circle(screen, c, t(400, 415), 1)
    pygame.draw.line(screen, c, t(400, 415), t(365, 370), width=1)

    #pygame.draw.circle(screen, c, t(365, 370), 1)
    pygame.draw.line(screen, c, t(365, 370), t(345, 370), width=1)
    #pygame.draw.circle(screen, c, t(345, 370), 1)
    pygame.draw.line(screen, c, t(345, 370), t(365, 340), width=1)
    #pygame.draw.circle(screen, c, t(365, 340), 1)
    pygame.draw.line(screen, c, t(365, 340), t(400, 350), width=1)
    #pygame.draw.circle(screen, c, t(400, 350), 1)
    pygame.draw.line(screen, c, t(400, 350), t(440, 385), width=1)
    #pygame.draw.circle(screen, c, t(440, 385), 1)
    pygame.draw.line(screen, c, t(440, 385), t(480, 400), width=1)
    #pygame.draw.circle(screen, c, t(480, 400), 1)
    pygame.draw.line(screen, c, t(480, 400), t(520, 385), width=1)

    #pygame.draw.circle(screen, c, t(520, 385), 1)
    pygame.draw.line(screen, c, t(520, 385), t(575, 350), width=1)
    #pygame.draw.circle(screen, c, t(575, 350), 1)
    pygame.draw.line(screen, c, t(575, 350), t(595, 340), width=1)
    #pygame.draw.circle(screen, c, t(595, 340), 1)
    pygame.draw.line(screen, c, t(595, 340), t(640, 370), width=1)
    #pygame.draw.circle(screen, c, t(640, 370), 1)
    pygame.draw.line(screen, c, t(640, 370), t(610, 385), width=1)
    #pygame.draw.circle(screen, c, t(610, 385), 1)
    pygame.draw.line(screen, c, t(610, 385), t(560, 430), width=1)
    #pygame.draw.circle(screen, c, t(560, 430), 1)
    pygame.draw.line(screen, c, t(560, 430), t(535, 430), width=1)
    #pygame.draw.circle(screen, c, t(535, 430), 1)
    pygame.draw.line(screen, c, t(535, 430), t(480, 425), width=1)

    #
    # GLASSES
    #
    # Circle outlines
    pygame.draw.circle(screen, c, t(430, 335), 36, width=1)
    pygame.draw.circle(screen, c, t(530, 335), 36, width=1)

    # Bridge and sides
    pygame.draw.line(screen, c, t(430, 335), t(530, 335), width=1)
    pygame.draw.line(screen, c, t(430, 325), t(365, 340), width=1)
    pygame.draw.line(screen, c, t(530, 325), t(595, 340), width=1)

    # Fill with black circles
    color_black = (0, 0, 0)
    pygame.draw.circle(screen, color_black, t(430, 335), 35)
    pygame.draw.circle(screen, color_black, t(530, 335), 35)

    # Left side lines over black area
    pygame.draw.line(screen, c, t(405, 350), t(430, 350), width=1)
    pygame.draw.line(screen, c, t(405, 342), t(445, 342), width=1)
    pygame.draw.line(screen, c, t(405, 334), t(425, 334), width=1)
    pygame.draw.line(screen, c, t(405, 326), t(450, 326), width=1)
    pygame.draw.line(screen, c, t(405, 318), t(415, 318), width=1)
    pygame.draw.line(screen, c, t(430, 318), t(445, 318), width=1)

    # Right side code block
    pygame.draw.line(screen, c, t(515, 360), t(530, 360), width=1)
    pygame.draw.line(screen, c, t(515, 352), t(525, 352), width=1)
    pygame.draw.line(screen, c, t(515, 344), t(535, 344), width=1)
    pygame.draw.line(screen, c, t(515, 336), t(530, 336), width=1)
    pygame.draw.line(screen, c, t(540, 336), t(550, 336), width=1)
    pygame.draw.line(screen, c, t(515, 328), t(530, 328), width=1)
    pygame.draw.line(screen, c, t(515, 320), t(535, 320), width=1)
    pygame.draw.line(screen, c, t(515, 312), t(530, 312), width=1)

    #
    # DRIP
    #
    pygame.draw.line(screen, c, t(580, 260), t(675, 240), width=1)
    pygame.draw.line(screen, c, t(380, 260), t(290, 240), width=1)