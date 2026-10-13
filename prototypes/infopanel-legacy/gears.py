
#Gear Functions
#Part of Monkey Butler 3
#fucking gears bro on law

import math
import pygame

def gear_draw(screen, degrees, color, x, y):
    """
    Draws one 'gear wedge' polygon at (x, y) with a given angle (degrees).
    Replaces arcade.draw_polygon_filled with pygame.draw.polygon.
    """
    radius = 75

    # Converts degrees to radians
    def get_theta(d):
        return d * (math.pi / 180)

    # Returns the x-coordinate for a given angle & radius
    def get_x(theta, r):
        return math.cos(theta) * r + x

    # Returns the y-coordinate for a given angle & radius
    def get_y(theta, r):
        return math.sin(theta) * r + y

    # Compute the corners of the polygon wedge
    xOne = get_x(get_theta(degrees - 15), radius)
    yOne = get_y(get_theta(degrees - 15), radius)

    xTwo = get_x(get_theta(degrees + 15), radius)
    yTwo = get_y(get_theta(degrees + 15), radius)

    xThree = get_x(get_theta(degrees - 12), radius + 15)
    yThree = get_y(get_theta(degrees - 12), radius + 15)

    xFour = get_x(get_theta(degrees + 12), radius + 15)
    yFour = get_y(get_theta(degrees + 12), radius + 15)

    # The four points of the wedge
    point_list = (
        (xOne,  yOne),
        (xTwo,  yTwo),
        (xFour, yFour),
        (xThree, yThree)
    )

    # Draw the filled polygon in PyGame
    pygame.draw.polygon(screen, color, point_list)


def gear_place(screen, degrees, color, x, y):
    """
    Draws multiple 'gear wedge' polygons around a central point
    at angles offset by 45 degrees from each other.
    """
    gear_draw(screen, degrees,   color, x, y)
    gear_draw(screen, degrees-45,  color, x, y)
    gear_draw(screen, degrees-90,  color, x, y)
    gear_draw(screen, degrees-135, color, x, y)
    gear_draw(screen, degrees-180, color, x, y)
    gear_draw(screen, degrees-225, color, x, y)
    gear_draw(screen, degrees-270, color, x, y)
    gear_draw(screen, degrees-315, color, x, y)

    pygame.draw.circle(screen, color, (x, y), 75, width=6)

    #x = int(get_x * scale_x)
   # y = int(get_y * scale_y)
   # radius = int(75 * (scale_x + scale_y) / 2)  # approximate scaling
   # pygame.draw.circle(screen, color, (x, y), radius, width=5)