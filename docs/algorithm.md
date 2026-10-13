# How HeavyKeeper works

This page explains the algorithm with diagrams, in the order the data flows. The worked examples
(sections 3, 5, 7 and 8) are replayed through the real code by the `>>>` examples on this page,
which the test suite runs, and a test checks the decay chart's data against the library. The bucket
grid in section 2 is only an illustration. The examples reach into private classes
(`topk._sketch.PySketch`) to stage scenarios; that is for explanation only and is not public API.

## In plain words

The problem: keep the `k` most frequent keys of a stream that may hold millions of distinct keys,
in memory that does not grow. A `Counter` needs one entry per distinct key.

Picture a notice board with a fixed number of slots and a crowd of people each calling out a word.
Whenever a word is called, it goes to a slot chosen from the word itself. If the slot already shows
that word, its tally goes up by one. If the slot shows a *different* word, that other word's tally
may be knocked down by one, but only sometimes, and the bigger the tally the less likely it is. A
word called once or twice is soon wiped out; a word called thousands of times is practically
permanent. In the end the biggest tallies on the board belong to the most frequent words.

The board is the **sketch**. Because a slot only remembers a short stamp of the word (not the word),
a second, small structure, the **top-k set**, keeps the names of the `k` words that currently have
the biggest tallies, so they can be reported by name. The rest of this page makes that precise.

**Vocabulary.** A *key* is one of the things you count (the paper calls it a *flow*), and an
*arrival* is one occurrence of a key in the stream (the paper says *packet*). A frequent key is an
*elephant* and a rare one a *mouse*. A key is *monitored* when it is currently in the top-k set. `k`
is how many keys you want, `d` (`depth`) the number of rows of the sketch and `w` (`width`) the
number of buckets per row. `b` is `decay_base`. `n_min` is the smallest count in the top-k set.

## 1. One `add()` from end to end

```mermaid
flowchart TD
    K(["key"]) --> E["1. encode_key<br/>key to bytes, stable across processes<br/>(never Python's hash())"]
    E --> L["2. locate<br/>BLAKE2b once: one bucket in each row<br/>plus a fingerprint"]
    L --> I["3. sketch.insert(slots, fp, monitored, n_min)<br/>returns maxv, this key's estimate now"]
    I --> U["4. update the top-k set"]
    U --> M(["most_common() returns<br/>[(key, count), ...] largest first"])
    S[("sketch<br/>d x w buckets of (fingerprint, counter)")] <--> I
    T[("top-k set<br/>min-heap of at most k (key, count)")] <--> U
    T -. "monitored: is the key in the set?<br/>n_min: smallest count, 0 until full" .-> I
```

Steps 3 and 4 are the algorithm; sections 3 to 8 take them apart. `add(key, count)` runs steps 3
and 4 `count` times but steps 1 and 2 only once.

## 2. The sketch: buckets and hashing

The sketch is `d` rows of `w` buckets. Each bucket holds a **fingerprint** (a few bits of the key's
hash, used to tell keys apart without storing them) and a **counter**. A key maps to exactly one
bucket in each row, and all `d` positions come from a single hash of the key:

```mermaid
flowchart LR
    B["key bytes"] --> H["BLAKE2b, 128 bits,<br/>keyed by the seed"]
    H --> X["x: 128 bits"]
    X --> H1["bits 0 to 63:<br/>h1"]
    X --> H2["bits 64 to 95:<br/>h2, forced odd"]
    X --> FP["bits 96 to 127:<br/>fingerprint F<br/>(low fingerprint_bits bits)"]
    H1 --> P["row j uses bucket<br/>idx_j = (h1 + j * h2) mod w<br/>one hash, d positions"]
    H2 --> P
```

A bucket is a (fingerprint, counter) pair, 4 + 8 = 12 bytes. This picture is an illustration (the
values are made up): a key with fingerprint `F1` lands on the highlighted bucket of each row:

```mermaid
flowchart TB
    K(["a key with fingerprint F1"])
    subgraph row0["row 0 (each bucket is fingerprint : counter)"]
        direction LR
        a0["F4 : 3"] ~~~ a1["F1 : 21"] ~~~ a2["F2 : 14"] ~~~ a3["F9 : 1"] ~~~ a4["..."]
    end
    subgraph row1["row 1"]
        direction LR
        b0["F1 : 22"] ~~~ b1["F2 : 14"] ~~~ b2["F5 : 1"] ~~~ b3["F4 : 2"] ~~~ b4["..."]
    end
    K --> row0
    row0 ~~~ row1
    classDef chosen fill:#fde68a,stroke:#b45309,color:#000
    class a1,b0 chosen
```

The buckets are stored flat, as `fps[j * w + idx]` and `counts[j * w + idx]`, in two compact
`array` objects.

Many keys share every bucket, so a bucket cannot tell which key its counter belongs to. The
fingerprint says which key *last claimed* it, and the rules below keep the counter honest about
that key. The sketch never stores a key, only a fingerprint, which is why its size is fixed.

## 3. Inserting into a bucket

For each of its `d` buckets, a key follows one of three rules, depending on what the bucket holds.
Each bucket then contributes a number to the key's estimate (the diagram calls it "adds"), and the
estimate `maxv` that comes back is the largest of the `d` contributions.

```mermaid
flowchart TD
    A(["Bucket holds (FP, C). This key has fingerprint F."]) --> B{"C = 0 ?"}
    B -- yes --> C1["Claim it: FP = F, C = 1<br/>adds 1 to the estimate"]
    B -- no --> D{"FP = F ?"}
    D -- yes --> E{"key is monitored<br/>or C ≤ n_min ?"}
    E -- yes --> F1["C = C + 1<br/>adds C"]
    E -- no --> G["Leave it (Optimization II):<br/>probably another key's bucket<br/>adds C"]
    D -- no --> H{"draw u in [0,1):<br/>u < b^-C ?"}
    H -- no --> I["Leave it<br/>adds 0"]
    H -- yes --> J["C = C - 1"]
    J --> K{"C = 0 ?"}
    K -- yes --> L["Take it over: FP = F, C = 1<br/>adds 1"]
    K -- no --> M["Resident survives<br/>adds 0"]
```

- **Empty bucket:** claim it.
- **Same fingerprint:** it is (almost certainly) this key's own bucket, so count the occurrence.
  The right-hand condition is Optimization II, explained in section 8.
- **Different fingerprint (a collision):** another key owns the bucket. With probability `b^-C` the
  resident's counter loses one; if that reaches zero the newcomer takes the bucket over. Otherwise
  nothing changes. This decay is the heart of the algorithm; section 4 shows why it works.

The three outcomes of a collision, replayed through the real kernel with a scripted random draw
(`C = 5` gives `b^-C = 0.68`):

```pycon
>>> import random
>>> from array import array
>>> from topk import TopK
>>> from topk._sketch import PySketch
>>> class Script(random.Random):
...     """A Random that replays chosen draws (one is taken per collision)."""
...
...     def __new__(cls, *draws):
...         return super().__new__(cls)
...
...     def __init__(self, *draws):
...         super().__init__(0)
...         self.draws = list(draws)
...
...     def random(self):
...         return self.draws.pop(0)
>>> def collide(counter, draw):
...     """Insert key "a" where another key's fingerprint holds `counter`."""
...     sketch = PySketch(1, 1, 1.08, 32, 7, rng=Script(draw))
...     slots, fp = sketch.locate(b"a")
...     sketch.fps[0] = (fp + 1) % 2**32  # a different fingerprint
...     sketch.counts[0] = counter
...     adds = sketch.insert(slots, fp, True, 0)
...     return adds, sketch.counts[0], sketch.fps[0] == fp
>>> collide(counter=5, draw=0.99)  # u >= b^-C: nothing happens
(0, 5, False)
>>> collide(counter=5, draw=0.0)  # u < b^-C: the resident loses one and survives
(0, 4, False)
>>> collide(counter=1, draw=0.0)  # the resident reaches 0: the newcomer takes the bucket
(1, 1, True)
```

## 4. Why decay separates mice from elephants

A mouse's bucket holds a small counter, so a colliding arrival decays it almost every time and the
mouse is gone after a few arrivals. An elephant's counter is large, so decay is rare. With
`b = 1.08`:

```mermaid
%%{init: {"themeVariables": {"xyChart": {"plotColorPalette": "#2563eb"}}}}%%
xychart-beta
    title "Chance that a colliding arrival decays a counter (b = 1.08)"
    x-axis "counter C of the bucket's current owner" [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    y-axis "probability b^-C" 0 --> 1
    line [1.0, 0.4632, 0.2145, 0.0994, 0.046, 0.0213, 0.0099, 0.0046, 0.0021, 0.001, 0.0005]
```

Read it as: a counter of 1 is knocked down almost every time a different key collides with it
(0.93); a counter of 50 only about one time in 47; a counter of 100 about one time in 2,200. From
`C = 478` on, `b^-C` is below `2^-53` and no random draw is taken at all. The numbers behind the
chart, and how many colliding arrivals it takes on average to remove one count:

```pycon
>>> b = 1.08
>>> for c in (1, 5, 10, 20, 50, 100, 200):
...     print(f"{c:>4}  {b**-c:<10.3g}  {b**c:>12,.1f}")
   1  0.926             1.1
   5  0.681             1.5
  10  0.463             2.2
  20  0.215             4.7
  50  0.0213           46.9
 100  0.000455       2,199.8
 200  2.07e-07   4,838,949.6
>>> PySketch(1, 1, 1.08, 32, 1).decay_cutoff
478
```

So an elephant that has built up a counter keeps it through a flood of colliding mice, while a
mouse that wandered into an elephant's bucket cannot dislodge it. A larger `decay_base` makes small
counters fade faster; the paper uses 1.08.

## 5. Worked example: the paper's Figure 1

An arrival of key `f3` (fingerprint `F3`), which is already in the top-k set: It maps to one
bucket in each of three rows:

```mermaid
flowchart LR
    P(["an arrival of f3<br/>fingerprint F3, f3 is already in the top-k set"])
    P --> R0["row 0: bucket (F4, 21)<br/>collision: decay chance 1.08^-21 = 0.199<br/>the draw misses"]
    P --> R1["row 1: bucket (F1, 1)<br/>collision: decay chance 1.08^-1 = 0.926<br/>the draw hits, the counter falls to 0"]
    P --> R2["row 2: bucket (F3, 7)<br/>same fingerprint, f3 is monitored"]
    R0 --> A0["unchanged: (F4, 21)<br/>adds 0"]
    R1 --> A1["f3 takes the bucket: (F3, 1)<br/>adds 1"]
    R2 --> A2["incremented: (F3, 8)<br/>adds 8"]
    A0 --> M["maxv = max(0, 1, 8) = 8"]
    A1 --> M
    A2 --> M
    M --> T(["f3 is monitored, so the set<br/>raises its count to 8"])
```

The same walk through the kernel, with the draws scripted as 0.5 (above `0.199`, so no decay) and
0.0 (below `0.926`, so decay):

```pycon
>>> sketch = PySketch(
...     width=1, depth=3, decay_base=1.08, fingerprint_bits=32, seed=7, rng=Script(0.5, 0.0)
... )
>>> slots, f3 = sketch.locate(b"f3")
>>> sketch.fps[:] = array("I", [f3 + 1, f3 + 2, f3])  # F4, F1, F3
>>> sketch.counts[:] = array("Q", [21, 1, 7])
>>> sketch.insert(slots, f3, True, 0)  # maxv
8
>>> list(sketch.counts)
[21, 1, 8]
>>> [fp == f3 for fp in sketch.fps]  # f3 now owns rows 1 and 2
[False, True, True]
>>> round(sketch.decay_probability(21), 3), round(sketch.decay_probability(1), 3)  # as in the diagram
(0.199, 0.926)
```

Each key is stored in `d` buckets, and the estimate is the largest of its counters. Using several
rows guards against the unlucky case where one of a key's buckets is a poor home (for example it
was taken over, as in row 1 here): another row will usually still hold a good count.

## 6. The top-k set

The sketch only produces an estimate. A second structure, a min-heap of at most `k` keys with
their counts, decides who is in the top `k`. After each insertion the set looks at `maxv`:

```mermaid
flowchart TD
    A(["maxv returned by the sketch"]) --> B{"key already in the set?"}
    B -- yes --> C1["Raise its count to max(old, maxv)"]
    B -- no --> D{"maxv = 0 ?"}
    D -- yes --> E["Ignore: a key is never admitted at 0"]
    D -- no --> F{"set has fewer than k keys?"}
    F -- yes --> G["Add (key, maxv)"]
    F -- no --> H{"maxv = n_min + 1 ?"}
    H -- yes --> I["Evict the smallest, add (key, maxv)<br/>Optimization I"]
    H -- no --> J["Reject"]
```

**Why a full set admits only at exactly `n_min + 1` (Optimization I).** A newcomer must beat the
smallest entry, so `maxv` below `n_min + 1` is rejected. But a genuine newcomer's counter climbs by
one per arrival, and only while it is `≤ n_min` (Optimization II), so it can only ever arrive at
exactly `n_min + 1`. A larger value cannot come from a genuine newcomer: it means the key landed on
another key's bucket through a fingerprint collision (the paper's Theorem 1). Rejecting it makes
it much harder for a mouse to borrow an elephant's count.

## 7. A newcomer climbs into a full set

Here `k = 2`, the set holds `{a: 5, b: 5}` (so `n_min = 5`), and a new key `c` arrives six times
with no collisions:

```mermaid
flowchart TB
    subgraph ok["this library: increment while C <= n_min"]
        direction LR
        p1["arrival 1<br/>empty, claimed<br/>(Fc, 1)<br/>maxv 1"] --> p2["arrival 2<br/>1 to 2<br/>maxv 2"] --> p3["arrival 3<br/>2 to 3<br/>maxv 3"] --> p4["arrival 4<br/>3 to 4<br/>maxv 4"] --> p5["arrival 5<br/>4 to 5<br/>maxv 5"] --> p6["arrival 6<br/>5 to 6 (5 <= 5)<br/>maxv 6 = n_min + 1<br/>ADMITTED"]
    end
    subgraph typo["the paper as printed: increment while C < n_min"]
        direction LR
        q5["arrivals 1 to 5<br/>as above<br/>maxv 5"] --> q6["arrival 6<br/>5 < 5 is false: no increment<br/>maxv stays 5, never reaches 6<br/>NEVER ADMITTED"]
    end
    ok ~~~ typo
```

The paper prints the test as `C < n_min`. With that strict comparison arrival 6 would not
increment (5 < 5 is false), `maxv` would stay at 5, and `c` would never be admitted however often it
appears. The authors' code, and this library, use `C <= n_min`.

```pycon
>>> top = TopK(2, width=4096, seed=1)
>>> top.add("a", 5)
>>> top.add("b", 5)
>>> for arrival in range(1, 7):
...     top.add("c")
...     print(arrival, top.estimate("c"), "c" in top)
1 1 False
2 2 False
3 3 False
4 4 False
5 5 False
6 6 True
```

## 8. Fingerprint collisions, and why the two optimizations exist

Two different keys that land in the same bucket *and* have the same fingerprint look identical to
that bucket, and the sketch cannot tell them apart. Given two keys in one bucket, the chance that
their fingerprints match is 1 in 2^bits: about one in four billion with the default 32-bit
fingerprints, one in 256 with 8-bit ones. (Across a whole stream some pair of keys will share a
fingerprint somewhere: with 100,000 distinct keys it is more likely than not. That matters only if
the pair also shares a bucket.) With short fingerprints the problem is common, which is where the
optimizations earn their keep:

```mermaid
flowchart TD
    S["The set is full, n_min = 3. A bucket holds (F, 50): an elephant.<br/>Key m, not monitored, lands on it with the same fingerprint F."]
    S --> B1["BASIC ALGORITHM"]
    S --> B2["THIS LIBRARY"]
    B1 --> b1["the bucket is incremented: (F, 51)"]
    b1 --> b2["maxv = 51 is more than n_min"]
    b2 --> b3["m is admitted with count 51<br/>(its true count is 1)"]
    B2 --> c1["Optimization II: 50 is more than n_min and m is not monitored,<br/>so the bucket stays (F, 50)"]
    c1 --> c2["maxv = 50 is not n_min + 1 = 4,<br/>so Optimization I rejects m"]
    c2 --> c3["m stays out;<br/>the elephant keeps 50"]
```

Measured with 8-bit fingerprints on a 500,000-item Zipf stream, `k = 100`:

| | precision | ARE (average relative error) |
|---|---|---|
| basic algorithm | 0.75 to 0.82 | 385 to 800 |
| this library | 0.99 to 1.00 | about 0.005 |

With 32-bit fingerprints the two give identical results.

Staging that collision (this pokes at private state to set the scene):

```pycon
>>> def collision(cls):
...     """A full set (n_min = 3) and a bucket holding an elephant (50) under m's fingerprint."""
...     tk = cls(2, width=1, depth=1, seed=1)
...     tk._heap.push("b", 3)
...     tk._heap.push("c", 3)
...     slots, fp = tk._sketch.locate(tk._encode("m"))
...     tk._sketch.fps[slots[0]] = fp
...     tk._sketch.counts[slots[0]] = 50
...     return tk
>>> class Basic(TopK):  # the plain algorithm, without Optimizations I and II
...     _selective_increment = False
...     _exact_admission = False
>>> full = collision(TopK)
>>> full.add("m")
>>> "m" in full, full._sketch.counts[0]  # rejected, and the elephant's count is untouched
(False, 50)
>>> basic = collision(Basic)
>>> basic.add("m")
>>> ("m", 51) in basic.most_common()  # a mouse reported at 51 after one arrival
True
```

## Where to go next

- The README covers the API, parameters (`width`, `depth`, `decay_base`, `fingerprint_bits`),
  memory, and the measured accuracy and speed.
- [docs/examples.md](examples.md) has runnable examples and recipes.
- The paper: Gong et al., *HeavyKeeper: An Accurate Algorithm for Finding Top-k Elephant Flows*,
  USENIX ATC 2018, <https://www.usenix.org/conference/atc18/presentation/gong>. Section 3 is the
  algorithm and its optimizations; Section 4 proves the no-overestimation and error bounds.
- The README's "Differences from the paper" lists where this implementation deliberately departs
  from the paper's pseudocode, including the `C < n_min` typo shown in section 7.
