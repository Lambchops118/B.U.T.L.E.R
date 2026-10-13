# Function to create text box with wrapped text in pygame

import pygame
import os
import sys

#font_path = r"C:\Users\aljac\Desktop\Talos\InfoPanel\VT323-Regular.ttf"

def render_textrect(text, x, y, width, height, size, color, target, font_path):
        font = pygame.font.Font(font_path, size)

        # Word wrap text into a list of lines
        words   = text.split(" ")
        lines   = []
        current = ""

        for word in words:
            test = current + word + " "
            if font.size(test)[0] <= width:
                current = test
            else:
                lines.append(current)
                current = word + " "
        lines.append(current)

        # Create the text block surface
        surf = pygame.Surface((width, height), pygame.SRCALPHA)

        line_height = font.get_linesize()
        ty = 0

        for line in lines:
            if ty + line_height > height:
                break
            text_surf = font.render(line, True, color)
            surf.blit(text_surf, (0, ty))
            ty += line_height

        # Blit to target surface at (x, y)
        target.blit(surf, (x, y))

        return surf