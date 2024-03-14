import pyttsx3
import PyPDF2
from tkinter.filedialog import *

book = askopenfilename()
pdfReader = PyPDF2.PdfReader(book)
page_num = len(pdfReader.pages)

for num in range(0, page_num):
    page = pdfReader.pages[num]
    text = page.extract_text()
    player = pyttsx3.init()
    player.say(text)
    player.runAndWait()
    
