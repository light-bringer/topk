"""The most frequent words of a text: the smallest useful TopK program.

python examples/word_frequency.py
"""

import re

from topk import TopK

# The opening sentence of "A Tale of Two Cities" (Charles Dickens, 1859; public domain).
TEXT = (
    "It was the best of times, it was the worst of times, it was the age of wisdom, it was the "
    "age of foolishness, it was the epoch of belief, it was the epoch of incredulity, it was the "
    "season of Light, it was the season of Darkness, it was the spring of hope, it was the winter "
    "of despair, we had everything before us, we had nothing before us, we were all going direct "
    "to Heaven, we were all going direct the other way - in short, the period was so far like the "
    "present period, that some of its noisiest authorities insisted on its being received, for "
    "good or for evil, in the superlative degree of comparison only."
)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def main() -> list[tuple[str, int]]:
    top: TopK[str] = TopK(5, seed=1)
    top.update(words(TEXT))  # any iterable of keys; a mapping of key -> count also works
    for word, count in top.most_common():
        print(f"{count:>3}  {word}")
    return top.most_common()


if __name__ == "__main__":
    main()
