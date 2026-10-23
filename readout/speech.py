"""Background text-to-speech worker.

pyttsx3 engines are not thread-safe and on Windows live inside a COM
apartment, so one dedicated thread owns the engine and everything else talks
to it through a command queue. Word boundaries, completion and export
progress flow back to the UI through the ``events`` queue.

Every speak/stop call bumps a generation counter. The engine checks it on
each word boundary and stops itself as soon as its job is out of date, which
is what makes pause, seek and live speed changes feel instant.
"""

import os
import queue
import shutil
import tempfile
import threading
import warnings
import wave

# comtypes warns about enum changes on brand-new Pythons; it works fine for SAPI.
warnings.filterwarnings("ignore", message=".*comtypes", category=FutureWarning)


class SpeechWorker(threading.Thread):
    def __init__(self, events):
        super().__init__(daemon=True, name="speech")
        self.events = events
        self.engine = None
        self._commands = queue.Queue()
        self._lock = threading.Lock()
        self._gen = 0
        self._job = (0, None, 0)  # (generation, tag, char offset of the utterance)

    # -- API, safe to call from any thread ---------------------------------

    def speak(self, text, *, base, tag, rate, volume, voice):
        gen = self._bump()
        self._commands.put(("speak", gen, dict(text=text, base=base, tag=tag,
                                               rate=rate, volume=volume, voice=voice)))

    def export(self, chunks, path, *, tag, rate, volume, voice):
        gen = self._bump()
        self._commands.put(("export", gen, dict(chunks=chunks, path=path, tag=tag,
                                                rate=rate, volume=volume, voice=voice)))

    def stop(self):
        self._bump()

    def shutdown(self):
        self._bump()
        self._commands.put(("quit", 0, None))

    # -- worker thread -----------------------------------------------------

    def _bump(self):
        with self._lock:
            self._gen += 1
            return self._gen

    def run(self):
        try:
            try:
                import comtypes  # SAPI5 needs COM initialised on this thread
                comtypes.CoInitialize()
            except ImportError:
                pass
            import pyttsx3
            self.engine = pyttsx3.init()
        except Exception as exc:
            self.events.put(("error", f"Text-to-speech is unavailable: {exc}"))
            return

        self.engine.connect("started-word", self._on_word)
        self.events.put(("voices", [(v.id, v.name) for v in self.engine.getProperty("voices")]))

        while True:
            kind, gen, job = self._commands.get()
            if kind == "quit":
                break
            if gen != self._gen:
                continue  # superseded before it started
            try:
                if kind == "speak":
                    self._speak(gen, **job)
                else:
                    self._export(gen, **job)
            except Exception as exc:
                self.events.put(("error", str(exc)))

    def _on_word(self, name=None, location=0, length=0):
        gen, tag, base = self._job
        if gen != self._gen:
            self.engine.stop()
        # pyttsx3's SAPI5 driver also fires "started-word" on stream start, with
        # the stream number as the location; real word events carry the word.
        elif tag is not None and name is not None:
            self.events.put(("word", tag, base + location))

    def _apply(self, rate, volume, voice):
        self.engine.setProperty("rate", rate)
        self.engine.setProperty("volume", volume)
        if voice:
            self.engine.setProperty("voice", voice)

    def _speak(self, gen, text, base, tag, rate, volume, voice):
        self._apply(rate, volume, voice)
        self._job = (gen, tag, base)
        self.engine.say(text)
        self.engine.runAndWait()
        self.events.put(("done", tag, gen == self._gen))

    def _export(self, gen, chunks, path, tag, rate, volume, voice):
        self._apply(rate, volume, voice)
        self._job = (gen, None, 0)
        tmp = tempfile.mkdtemp(prefix="readout-")
        try:
            parts = []
            for i, text in enumerate(chunks):
                if gen != self._gen:
                    self.events.put(("export_done", tag, None))
                    return
                if text.strip():
                    part = os.path.join(tmp, f"{i:05d}.wav")
                    self._save(text, part)
                    if os.path.exists(part) and os.path.getsize(part) > 44:
                        parts.append(part)
                self.events.put(("export_progress", tag, i + 1, len(chunks)))
            if gen != self._gen:
                self.events.put(("export_done", tag, None))
                return
            _concat_wav(parts, path)
            self.events.put(("export_done", tag, path))
        except Exception as exc:
            self.events.put(("export_done", tag, None))
            self.events.put(("error", f"Export failed: {exc}"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _save(self, text, path):
        sapi = getattr(getattr(self.engine.proxy, "_driver", None), "_tts", None)
        if sapi is None:
            self.engine.save_to_file(text, path)
            self.engine.runAndWait()
            return
        # On Windows, drive SAPI directly: pyttsx3's save loop can miss the
        # end-of-stream event and hang on the second file.
        import comtypes.client
        stream = comtypes.client.CreateObject("SAPI.SpFileStream")
        stream.Open(path, 3)  # SSFMCreateForWrite
        previous = sapi.AudioOutputStream
        sapi.AudioOutputStream = stream
        try:
            sapi.Speak(text, 0)  # synchronous
        finally:
            sapi.AudioOutputStream = previous
            stream.Close()


def _concat_wav(parts, dest):
    if not parts:
        raise RuntimeError("the selected pages have no readable text")
    with wave.open(parts[0], "rb") as first:
        params = first.getparams()
    with wave.open(dest, "wb") as out:
        out.setparams(params)
        for part in parts:
            with wave.open(part, "rb") as w:
                out.writeframes(w.readframes(w.getnframes()))
