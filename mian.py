import pyttsx3
import PyPDF2

book = open('HTRPLAB.pdf', 'rb')
pdfRead = PyPDF2.PdfReader(book) 
num_pages = len(pdfRead.pages)

speaker = pyttsx3.init()
page = pdfRead.pages[8]
text = page.extract_text()
print(text)
speaker.say(text)
speaker.runAndWait()
print(num_pages)

