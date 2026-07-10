# model-gauntlet results

Source: 154 run row(s), 154 passing, 154 judged.

## 1. Pass rate x effort ladder

| model | effort | n | pass | pass_rate | avg_quality(/10) |
| --- | --- | --- | --- | --- | --- |
| fable | medium | 40 | 40 | 100% | 8.42 |
| fable | high | 24 | 24 | 100% | 8.99 |
| hybrid | medium | 2 | 2 | 100% | 9.32 |
| sol | low | 40 | 40 | 100% | 9.04 |
| sol | medium | 24 | 24 | 100% | 9.26 |
| sol | high | 24 | 24 | 100% | 9.36 |

## 2. Tokens-per-pass efficiency frontier (money chart)

| model | effort | pass_rate | mean_tokens/run | tokens_per_pass |
| --- | --- | --- | --- | --- |
| fable | medium | 100% | 20,575 | 20,575 |
| fable | high | 100% | 21,049 | 21,049 |
| hybrid | medium | 100% | 22,663 | 22,663 |
| sol | low | 100% | 142,464 | 142,464 |
| sol | medium | 100% | 144,547 | 144,547 |
| sol | high | 100% | 189,087 | 189,087 |

## 3. Harness delta per model (bare -> harnessed)

| model | bare pass% | bare tok | harness pass% | harness tok | delta pass | delta tok |
| --- | --- | --- | --- | --- | --- | --- |
| fable | 100% | 20,875 | 100% | 20,315 | +0 pp | -560 |
| hybrid | - | - | 100% | 22,663 | - | - |
| sol | 100% | 144,353 | 100% | 215,977 | +0 pp | +71,624 |

## 4. Hybrid vs solo on T3

| model | kind | n | pass_rate | mean_tokens |
| --- | --- | --- | --- | --- |
| fable | solo | 4 | 100% | 18,809 |
| hybrid | hybrid | 2 | 100% | 22,663 |
| sol | solo | 4 | 100% | 149,702 |

## 5. Variance per cell (same prompt, N outcomes)

| cell (model/effort/harness) | task | reps | passed | loc min/med/max | tokens min/med/max |
| --- | --- | --- | --- | --- | --- |
| fable/high/bare | t1-py-a | 3 | 3/3 | 5/5/5 | 20,413/20,538/20,541 |
| fable/high/bare | t1-py-b | 3 | 3/3 | 7/7/7 | 18,581/20,520/20,915 |
| fable/high/bare | t1-ts-a | 3 | 3/3 | 7/7/7 | 18,435/20,340/20,418 |
| fable/high/bare | t1-ts-b | 3 | 3/3 | 6/11/14 | 21,102/21,379/21,406 |
| fable/high/bare | t2-py-a | 3 | 3/3 | 57/57/63 | 19,811/21,600/21,823 |
| fable/high/bare | t2-py-b | 3 | 3/3 | 42/50/52 | 20,376/22,259/22,531 |
| fable/high/bare | t2-ts-a | 3 | 3/3 | 30/32/36 | 22,269/22,984/23,308 |
| fable/high/bare | t2-ts-b | 3 | 3/3 | 39/43/49 | 20,394/21,430/21,802 |
| fable/medium/bare | t1-py-a | 3 | 3/3 | 5/5/5 | 20,313/20,507/20,609 |
| fable/medium/bare | t1-py-b | 3 | 3/3 | 7/7/7 | 18,573/20,376/20,524 |
| fable/medium/bare | t1-ts-a | 3 | 3/3 | 0/0/7 | 18,385/20,400/20,442 |
| fable/medium/bare | t1-ts-b | 3 | 3/3 | 11/12/14 | 20,938/20,956/21,135 |
| fable/medium/bare | t2-py-a | 3 | 3/3 | 49/51/58 | 19,940/21,153/22,695 |
| fable/medium/bare | t2-py-b | 3 | 3/3 | 35/38/38 | 19,716/21,742/21,799 |
| fable/medium/bare | t2-ts-a | 3 | 3/3 | 28/30/30 | 22,591/22,796/23,044 |
| fable/medium/bare | t2-ts-b | 3 | 3/3 | 39/40/42 | 20,213/21,620/22,310 |
| fable/medium/bare | t3-a | 2 | 2/2 | 119/122/125 | 16,643/17,910/19,176 |
| fable/medium/harness | t2-py-a | 3 | 3/3 | 53/55/55 | 20,294/20,375/20,605 |
| fable/medium/harness | t2-py-b | 3 | 3/3 | 49/54/62 | 20,447/20,941/21,141 |
| fable/medium/harness | t2-ts-a | 3 | 3/3 | 29/36/41 | 19,447/20,280/21,541 |
| fable/medium/harness | t2-ts-b | 3 | 3/3 | 38/42/45 | 19,376/20,098/20,448 |
| fable/medium/harness | t3-a | 2 | 2/2 | 123/132/140 | 19,555/19,708/19,861 |
| hybrid/medium/harness | t3-a | 2 | 2/2 | 158/167/176 | 22,377/22,663/22,949 |
| sol/high/bare | t1-py-a | 3 | 3/3 | 5/5/5 | 137,972/150,093/234,487 |
| sol/high/bare | t1-py-b | 3 | 3/3 | 7/7/7 | 133,196/171,567/201,633 |
| sol/high/bare | t1-ts-a | 3 | 3/3 | 7/7/9 | 121,838/182,515/227,452 |
| sol/high/bare | t1-ts-b | 3 | 3/3 | 4/8/8 | 175,795/211,781/217,282 |
| sol/high/bare | t2-py-a | 3 | 3/3 | 59/67/72 | 100,829/133,492/177,561 |
| sol/high/bare | t2-py-b | 3 | 3/3 | 53/53/56 | 121,466/168,923/293,604 |
| sol/high/bare | t2-ts-a | 3 | 3/3 | 27/31/33 | 224,235/240,132/357,301 |
| sol/high/bare | t2-ts-b | 3 | 3/3 | 48/51/52 | 161,991/162,400/230,542 |
| sol/low/bare | t1-py-a | 3 | 3/3 | 5/5/5 | 88,972/93,865/114,683 |
| sol/low/bare | t1-py-b | 3 | 3/3 | 7/7/7 | 92,278/92,411/127,790 |
| sol/low/bare | t1-ts-a | 3 | 3/3 | 7/9/10 | 92,925/97,609/98,734 |
| sol/low/bare | t1-ts-b | 3 | 3/3 | 4/8/9 | 91,873/92,828/112,701 |
| sol/low/bare | t2-py-a | 3 | 3/3 | 57/57/58 | 77,790/77,935/78,201 |
| sol/low/bare | t2-py-b | 3 | 3/3 | 44/47/50 | 98,699/114,301/118,591 |
| sol/low/bare | t2-ts-a | 3 | 3/3 | 27/27/36 | 96,159/96,166/184,875 |
| sol/low/bare | t2-ts-b | 3 | 3/3 | 41/48/49 | 97,191/98,372/136,948 |
| sol/low/bare | t3-a | 2 | 2/2 | 144/151/158 | 80,864/101,499/122,134 |
| sol/low/harness | t2-py-a | 3 | 3/3 | 62/63/65 | 150,444/168,864/189,669 |
| sol/low/harness | t2-py-b | 3 | 3/3 | 53/54/55 | 194,154/241,148/241,164 |
| sol/low/harness | t2-ts-a | 3 | 3/3 | 34/39/46 | 250,544/306,786/337,850 |
| sol/low/harness | t2-ts-b | 3 | 3/3 | 47/51/60 | 165,527/189,981/191,740 |
| sol/low/harness | t3-a | 2 | 2/2 | 149/153/157 | 196,665/197,904/199,144 |
| sol/medium/bare | t1-py-a | 3 | 3/3 | 5/5/7 | 76,114/132,517/137,880 |
| sol/medium/bare | t1-py-b | 3 | 3/3 | 7/7/7 | 113,331/135,760/178,602 |
| sol/medium/bare | t1-ts-a | 3 | 3/3 | 7/9/9 | 98,777/134,720/143,635 |
| sol/medium/bare | t1-ts-b | 3 | 3/3 | 4/8/9 | 134,966/155,816/157,951 |
| sol/medium/bare | t2-py-a | 3 | 3/3 | 58/58/64 | 99,688/99,872/151,044 |
| sol/medium/bare | t2-py-b | 3 | 3/3 | 50/53/60 | 142,374/164,661/166,938 |
| sol/medium/bare | t2-ts-a | 3 | 3/3 | 31/34/39 | 204,793/212,617/214,699 |
| sol/medium/bare | t2-ts-b | 3 | 3/3 | 47/51/54 | 117,920/138,738/155,707 |

## 6. When-to-use-which decision matrix + $/task

| model | best config | pass_rate | avg_quality(/10) | $/task (est) | when to use |
| --- | --- | --- | --- | --- | --- |
| fable | medium/harness | 100% | 8.60 | $0.4895 | reliable — default choice for this class |
| hybrid | medium/harness | 100% | 9.32 | $0.6894 | reliable — default choice for this class |
| sol | low/bare | 100% | 9.17 | $1.0666 | reliable — default choice for this class |


> `$/task` is a **list-price estimate** (runs execute on subscription, so no per-run billing); token counts are 0 under `--mock`.

