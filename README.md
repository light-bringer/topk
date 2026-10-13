# heavykeeper-topk

A pure-Python implementation of **HeavyKeeper** (Gong et al., USENIX ATC '18) for finding the
top-k most frequent items in a data stream with bounded memory.

> **Status: pre-alpha.** The public API is not implemented yet. See
> `docs/superpowers/specs/2026-10-13-heavykeeper-topk-design.md` for the design.

```python
from topk import TopK  # coming in 0.1.0
```

## Reference

Junzhi Gong, Tong Yang, Haowei Zhang, Hao Li, Steve Uhlig, Shigang Chen, Lorna Uden,
Xiaoming Li. *HeavyKeeper: An Accurate Algorithm for Finding Top-k Elephant Flows.*
USENIX ATC 2018. <https://www.usenix.org/conference/atc18/presentation/gong>

## License

MIT
