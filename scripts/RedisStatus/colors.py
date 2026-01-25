#!/usr/bin/env python
#Colors Module to color strings

import random

CYAN = '\033[96m'
GREEN = '\033[92m'
WARNING = '\033[93m'
FAIL = '\033[91m'
RED = '\033[91m'
END = '\033[0m'
PINK = '\033[35m'
BLUE = '\033[94m'
WHITE = '\033[37m'
BANNER="""
  ____          _ _       ____  _        _             
 |  _ \ ___  __| (_)___  / ___|| |_ __ _| |_ _   _ ___ 
 | |_) / _ \/ _` | / __| \___ \| __/ _` | __| | | / __|
 |  _ |  __| (_| | \__ \  ___) | || (_| | |_| |_| \__ \\
 |_| \_\___|\__,_|_|___/ |____/ \__\__,_|\__|\__,_|___/

"""

colorsList = ['\033[96m', '\033[92m','\033[91m', '\033[35m', '\033[94m']

def colorString(string, color):
        return color + string + END;

def getMatchingColor(memory, medium, high):
        color = WARNING
        if memory > high:
                color = FAIL
        elif memory < medium:
                color = GREEN
        return color

def getRoleColor(role):
        return GREEN if role == 'master' else BLUE

def getRandomColor():
	return random.choice(colorsList)

def getColorByIndex(index):
	return colorsList[index-1]

def colorWithBackground(string, rgbCode):
	back =  getColorEscape(rgbCode, True)
	front =  getColorEscape(rgbCode)
	return colorString(colorString(string, front), back) 

# RGB TO ESCAPE
def getColorEscape(rgbCode, bg = False):
	r,g,b = rgbCode
	return '\033[{};2;{};{};{}m'.format(48 if bg else 38, r, g ,b)
