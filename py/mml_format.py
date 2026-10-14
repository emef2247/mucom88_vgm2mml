"""Physical line formatting shared by bracket-repeat MML targets."""
import re


def wrap_track(track, body, width=110):
    """Start repeats on a fresh line and end lines after their repeat count."""
    lines, words = [], []

    def flush():
        if words:
            lines.append(track + ' ' + ' '.join(words))
            words.clear()

    for word in re.findall(r'\[|\]\d*|[^\s\[\]]+', body):
        if word == '[':
            flush()
        if words and len(track) + len(' '.join(words)) + len(word) + 2 > width:
            flush()
        words.append(word)
        if word.startswith(']'):
            flush()
    flush()
    return lines
