# Grizzlies eval — baseline

- Gemini stubbed: **True**
- Rows evaluated: 501
- Accuracy: **77.8%** (390/501)

## KPIs for 'never miss a trade'
- Missed actionable (truth=ENTRY/EXIT/TRIM, predicted=NOISE): **67**
- Wrong-direction closes (ENTRY↔EXIT): **1**
- False trades from noise (truth=NOISE, predicted=actionable): **29**

## Per-class recall / precision
| Class | Truth total | Pred total | TP | FN | FP | Recall | Precision |
|---|---|---|---|---|---|---|---|
| ENTRY | 120 | 113 | 87 | 33 | 26 | 72.5% | 77.0% |
| EXIT | 58 | 53 | 50 | 8 | 3 | 86.2% | 94.3% |
| TRIM | 171 | 145 | 130 | 41 | 15 | 76.0% | 89.7% |
| NOISE | 152 | 190 | 123 | 29 | 67 | 80.9% | 64.7% |

## Confusion (predicted → truth)
| predicted | truth | count |
|---|---|---|
| NOISE | TRIM | 32 |
| NOISE | ENTRY | 31 |
| ENTRY | NOISE | 18 |
| TRIM | NOISE | 10 |
| ENTRY | TRIM | 8 |
| TRIM | EXIT | 4 |
| NOISE | EXIT | 4 |
| EXIT | ENTRY | 1 |
| TRIM | ENTRY | 1 |
| EXIT | TRIM | 1 |
| EXIT | NOISE | 1 |

## Tier attribution (approximate — based on confidence)
- library_or_regex: 311
- none_or_conf_block: 170
- noise_short_circuit: 20

## Miss samples — by failure mode (up to 10 each)
### missed_entry (33 total)
- **ENTRY** → got **NOISE** (tier=none_or_conf_block) | msg=1471546335055581391 | reply=True
  - content: `Bought more CLSK here  , cpi tmm will be telling if I close this up before weekend.  ⏎  ⏎ Avg price for con is $42  ⏎  ⏎ <@&697950067285295115>`
  - reply_to: `CLSK 10c 2/20/26  ⏎  ⏎ @0.50 ⏎  ⏎ swinging this of course. I believe btc is due for a pump towards 68-70k wh`
- **ENTRY** → got **NOISE** (tier=none_or_conf_block) | msg=1478764398670643382 | reply=True
  - content: `Lightly risking here`
  - reply_to: `Short  btc  ⏎  ⏎ Entry:  ⏎ 1)72000 ⏎ 2)74000 ⏎  ⏎ Targets: ⏎ 1)71500 ⏎ 2)71000 ⏎ 3)70500 ⏎ 4)70000 ⏎ 5)69000 ⏎  ⏎ SL: 76000 ⏎ <@`
- **ENTRY** → got **NOISE** (tier=none_or_conf_block) | msg=1478788561024192702 | reply=True
  - content: `For the ones that missed the entry and want to get in, you can do this ⏎  ⏎ Entry:  ⏎ 1)73000 ⏎ 2)75000 ⏎ Stoploss: 77000 ⏎  ⏎ Targets just adjust it every $500 lower  ⏎ <@&697`
  - reply_to: `Short  btc  ⏎  ⏎ Entry:  ⏎ 1)72000 ⏎ 2)74000 ⏎  ⏎ Targets: ⏎ 1)71500 ⏎ 2)71000 ⏎ 3)70500 ⏎ 4)70000 ⏎ 5)69000 ⏎  ⏎ SL: 76000 ⏎ <@`
- **ENTRY** → got **NOISE** (tier=none_or_conf_block) | msg=1480965805595430943 | reply=True
  - content: `Crypto challenge account  ⏎  ⏎ Short  ⏎  ⏎ Coin: SUI ⏎  ⏎ Entry: ⏎ 1)1.00 ⏎  ⏎ leverage: 20 ⏎ Margin: $560 ⏎  ⏎ <@&697950067285295115>`
  - reply_to: `Loaded up the Crypto challenge account. Remember this is crypto leverage and i am using Bitunix for`
- **ENTRY** → got **NOISE** (tier=none_or_conf_block) | msg=1482010490971357336 | reply=False
  - content: `HIGH RISK LOTTO ⏎  ⏎ Hood 76c  same days  ⏎  ⏎ @0.80 ⏎  ⏎ either these bank or go worthless !  ⏎  ⏎ <@&697950067285295115>`
- **ENTRY** → got **NOISE** (tier=none_or_conf_block) | msg=1482026072911384607 | reply=False
  - content: `Lotto  ⏎  ⏎ Ibit 42p same days  ⏎  ⏎ @0.50  ⏎  ⏎ <@&697950067285295115>`
- **ENTRY** → got **NOISE** (tier=none_or_conf_block) | msg=1485663843333046292 | reply=True
  - content: `I added onto my hood short, avg cons are $145 <@&697950067285295115>`
  - reply_to: `Hood 71p 3/27/26 ⏎  ⏎ @1.65 ⏎  ⏎ playing a few cons here. Looking for the breakdown back below $71 and if be`
- **ENTRY** → got **EXIT** (tier=library_or_regex) | msg=1486000121601593457 | reply=True
  - content: `Ibit had a pre-market juicy pump, going to risk some more here and add into our Ibit calls. If you don’t want to risk more you can respectfully go by your risk `
  - reply_to: `Ibit 40c 3/27/26 ⏎  ⏎ @1.00 ⏎  ⏎ <@&697950067285295115>`
- **ENTRY** → got **NOISE** (tier=none_or_conf_block) | msg=1486349051136966687 | reply=False
  - content: `Crypto challenge account  ⏎  ⏎ Long ⏎  ⏎ Coin: TAO ⏎  ⏎ Entry: ⏎ 1)353.20 ⏎  ⏎ leverage: 20x ⏎ Margin: $669 ⏎  ⏎ <@&697950067285295115>`
- **ENTRY** → got **NOISE** (tier=none_or_conf_block) | msg=1486712107138285731 | reply=False
  - content: `Crypto challenge account  ⏎  ⏎ Long ⏎  ⏎ Coin: TAO ⏎  ⏎ Entry: ⏎ 1)339.5 ⏎  ⏎ leverage: 20x ⏎ Margin: $751 ⏎  ⏎ <@&697950067285295115>`

### missed_exit (8 total)
- **EXIT** → got **TRIM** (tier=library_or_regex) | msg=1480982184205619507 | reply=True
  - content: `Took the rest of the profits here for sui. Challenge account up 4%  ⏎ <@&697950067285295115>`
  - reply_to: `Crypto challenge account  ⏎  ⏎ Short  ⏎  ⏎ Coin: SUI ⏎  ⏎ Entry: ⏎ 1)1.00 ⏎  ⏎ leverage: 20 ⏎ Margin: $560 ⏎  ⏎ <@&697950067`
- **EXIT** → got **NOISE** (tier=none_or_conf_block) | msg=1485597520787935233 | reply=False
  - content: `I am canceling the long bid we had open at 66100, trump notified that he had good convos with Iran and will pause military action.  ⏎  ⏎ Going to observe the market`
- **EXIT** → got **NOISE** (tier=none_or_conf_block) | msg=1486727966359552102 | reply=True
  - content: `Full tp hit for TAO, netted $229 on the trade. I will update later where we stand on the challenge account ⏎ <@&697950067285295115>`
  - reply_to: `Trimmed half here, setting tp @$350 ⏎  ⏎ and stops at entry  ⏎  ⏎ <@&697950067285295115>`
- **EXIT** → got **NOISE** (tier=none_or_conf_block) | msg=1463158976832213184 | reply=True
  - content: `Stops got hit. Try to sneak one in. Possibly btc goes to that yearly open level and we see if a base is build up there to punt a long. Btc is building a pa rang`
  - reply_to: `Stoploss is the wick below. Scalp play here with tight TPs and stop. Let’s see if btc can drive a li`
- **EXIT** → got **TRIM** (tier=library_or_regex) | msg=1464777366755610895 | reply=True
  - content: `BANGGGGGG 1000% <@&697950067285295115>`
  - reply_to: `We got ourselves a BANGER! Left $100 on margin took 90% out <@&697950067285295115>`
- **EXIT** → got **TRIM** (tier=library_or_regex) | msg=1466461589367558204 | reply=True
  - content: `Hood puts up 100% BANGGGG ⏎  ⏎ $100 to $200 ⏎  ⏎ <@&697950067285295115>`
  - reply_to: `Hood 100p 1/30/26 ⏎  ⏎ @1.00 ⏎  ⏎ <@&697950067285295115>`
- **EXIT** → got **TRIM** (tier=library_or_regex) | msg=1466809434708639952 | reply=True
  - content: `101% BANGGGGG <@&697950067285295115>`
  - reply_to: `Hood 103c same days  ⏎  ⏎ @0.90 ⏎  ⏎ high risk lotto  ⏎ <@&697950067285295115>`
- **EXIT** → got **NOISE** (tier=none_or_conf_block) | msg=1470840055248715818 | reply=True
  - content: `Popped to entry smelled the first entry then died out <@&697950067285295115>`
  - reply_to: `Crypto play risky  ⏎  ⏎ Power long  ⏎  ⏎ Entry: ⏎ 1)0.39 ⏎ 2)0.37 ⏎  ⏎ targets:  ⏎ 1)0.39500 ⏎ 2)0.40000 ⏎ 3)0.40500 ⏎ 4)0.4`

### missed_trim (41 total)
- **TRIM** → got **ENTRY** (tier=library_or_regex) | msg=1471176785994580144 | reply=True
  - content: `TP1 hit <@&697950067285295115>`
  - reply_to: `Crypto play  ⏎  ⏎ Btc long ⏎  ⏎ Entry: ⏎ 1)66250 ⏎  ⏎ targets:  ⏎ 1)66800 ⏎ 2)67300 ⏎ 3)67900 ⏎ 4)68500 ⏎ 5)69000 ⏎  ⏎ <@&697950`
- **TRIM** → got **ENTRY** (tier=library_or_regex) | msg=1471591033866420365 | reply=True
  - content: `TP1 <@&697950067285295115>`
  - reply_to: `Crypto play  ⏎  ⏎ Btc long ⏎  ⏎ Entry: ⏎ 1)65350 ⏎ 2)63350 ⏎  ⏎ targets:  ⏎ 1)66000 ⏎ 2)66500 ⏎ 3)67000 ⏎ 4)67500 ⏎ 5)68000 ⏎ 6)`
- **TRIM** → got **ENTRY** (tier=library_or_regex) | msg=1471684170253340843 | reply=True
  - content: `TP2 <@&697950067285295115>`
  - reply_to: `Crypto play  ⏎  ⏎ Btc long ⏎  ⏎ Entry: ⏎ 1)65350 ⏎ 2)63350 ⏎  ⏎ targets:  ⏎ 1)66000 ⏎ 2)66500 ⏎ 3)67000 ⏎ 4)67500 ⏎ 5)68000 ⏎ 6)`
- **TRIM** → got **ENTRY** (tier=library_or_regex) | msg=1471860461229048004 | reply=True
  - content: `Tp3 on btc, <@&697950067285295115>`
  - reply_to: `Crypto play  ⏎  ⏎ Btc long ⏎  ⏎ Entry: ⏎ 1)65350 ⏎ 2)63350 ⏎  ⏎ targets:  ⏎ 1)66000 ⏎ 2)66500 ⏎ 3)67000 ⏎ 4)67500 ⏎ 5)68000 ⏎ 6)`
- **TRIM** → got **ENTRY** (tier=library_or_regex) | msg=1471881813269155840 | reply=True
  - content: `Tp5 did hit for btc, <@&697950067285295115>`
  - reply_to: `Crypto play  ⏎  ⏎ Btc long ⏎  ⏎ Entry: ⏎ 1)65350 ⏎ 2)63350 ⏎  ⏎ targets:  ⏎ 1)66000 ⏎ 2)66500 ⏎ 3)67000 ⏎ 4)67500 ⏎ 5)68000 ⏎ 6)`
- **TRIM** → got **NOISE** (tier=none_or_conf_block) | msg=1471888202179805368 | reply=True
  - content: `If anyone still holding CLSK those cons are sitting at $55  ⏎  ⏎ If you avg like I did yesterday your sitting nice and should be taking gains or trimming and settin`
  - reply_to: `If you avg on CLSK, contracts up 30%  ⏎  ⏎ If you didn’t, contracts are up 10% from $50 for you  ⏎ <@&6979`
- **TRIM** → got **ENTRY** (tier=library_or_regex) | msg=1475298545606070553 | reply=True
  - content: `TP4 BANGGGGGGG <@&697950067285295115>`
  - reply_to: `Crypto play  ⏎  ⏎ Btc short  ⏎  ⏎ Entry: ⏎ 1)67250 ⏎ 2)68850 ⏎  ⏎ targets:  ⏎ 1)66850 ⏎ 2)66450 ⏎ 3)66000 ⏎ 4)65500 ⏎ 5)64900`
- **TRIM** → got **NOISE** (tier=none_or_conf_block) | msg=1477711757660127334 | reply=False
  - content: `Make sure your trimming and setting stops now <@&697950067285295115>`
- **TRIM** → got **NOISE** (tier=none_or_conf_block) | msg=1478046515061592134 | reply=True
  - content: `20%  ⏎  ⏎ $110 to $132 ⏎  ⏎ These ran fast!  ⏎  ⏎ <@&697950067285295115>`
  - reply_to: `Ibit 38c 3/6/26 ⏎  ⏎ @1.10 ⏎  ⏎ <@&697950067285295115>`
- **TRIM** → got **NOISE** (tier=none_or_conf_block) | msg=1478047854248333342 | reply=True
  - content: `40% on ibit calls  ⏎  ⏎ $110 to $155 ⏎  ⏎ <@&697950067285295115>`
  - reply_to: `Ibit 38c 3/6/26 ⏎  ⏎ @1.10 ⏎  ⏎ <@&697950067285295115>`

### false_trade (29 total)
- **NOISE** → got **ENTRY** (tier=library_or_regex) | msg=1473333439712329921 | reply=False
  - content: `Crypto play  ⏎  ⏎ Space long ⏎  ⏎ Entry: ⏎ 1)0.010850 ⏎ 2)0.009850 ⏎  ⏎ targets:  ⏎ 1)0.011100 ⏎ 2)0.011500 ⏎ 3)0.012000 ⏎ 4)0.013000 ⏎ 5)0.014000 ⏎ 6)0.015000 ⏎  ⏎ <@&697950067285295115>`
- **NOISE** → got **ENTRY** (tier=library_or_regex) | msg=1473335805551313028 | reply=False
  - content: `Risky ⏎  ⏎ ibit 38.5c 2/20/26 ⏎  ⏎ @0.65 ⏎  ⏎ <@&697950067285295115>`
- **NOISE** → got **TRIM** (tier=library_or_regex) | msg=1474055742179901545 | reply=True
  - content: `Hood calls up 60%  ⏎  ⏎ $115 to $184  ⏎  ⏎ Let’s go!!!!! <@&697950067285295115>`
  - reply_to: `Hood 75c 2/20/26 ⏎  ⏎ @1.15 ⏎  ⏎ lightly play  ⏎  ⏎ <@&697950067285295115>`
- **NOISE** → got **TRIM** (tier=library_or_regex) | msg=1474056215884730562 | reply=False
  - content: `Hood calls up 80% BANGGGG  ⏎  ⏎ $115 to $207 ⏎ !!!!! <@&697950067285295115>`
- **NOISE** → got **TRIM** (tier=library_or_regex) | msg=1474437542890967183 | reply=True
  - content: `Hood calls up 15%  ⏎  ⏎ $55 to $63 ⏎  ⏎ These are running too fast , by the time I type  ⏎  ⏎ <@&697950067285295115>`
  - reply_to: `High risk  ⏎  ⏎ Hood 78c same day  ⏎  ⏎ @0.55 ⏎  ⏎ Lightly these can go to zero of course <@&697950067285295115>`
- **NOISE** → got **ENTRY** (tier=library_or_regex) | msg=1476247176517521408 | reply=True
  - content: `Taking ibit puts here too  ⏎  ⏎ 38p 2/27/26 ⏎  ⏎ @0.55  ⏎  ⏎ Risky but taking a punt here  ⏎ <@&697950067285295115>`
  - reply_to: `Hedging  a little with btc  ⏎  ⏎ Short ⏎  ⏎ Entry:  ⏎ 1)67350 ⏎  ⏎ Target: ⏎ 1)66950 ⏎ 2)66350 ⏎ 3)65750 ⏎ 4)65000 ⏎ 5)64500`
- **NOISE** → got **ENTRY** (tier=library_or_regex) | msg=1476650017212338327 | reply=False
  - content: `Ibit 38.5c 2/27/26 ⏎  ⏎ @0.30 ⏎  ⏎ <@&697950067285295115>`
- **NOISE** → got **TRIM** (tier=library_or_regex) | msg=1478107023051849883 | reply=True
  - content: `The ones still holding ibit puts, those are up 20%, this is where you trim and set stops <@&697950067285295115>`
  - reply_to: `Ibit 39p 3/6/26 ⏎  ⏎ @0.82 ⏎  ⏎ risking ibit with btc  ⏎ <@&697950067285295115>`
- **NOISE** → got **ENTRY** (tier=library_or_regex) | msg=1478107213733040239 | reply=True
  - content: `Avg entry on btc is printing <@&697950067285295115>`
  - reply_to: `Hedging  a little with btc  ⏎  ⏎ Short ⏎  ⏎ Entry:  ⏎ 1)68500 ⏎ 2)69500 ⏎  ⏎ Target: ⏎ 1)68000 ⏎ 2)67500 ⏎ 3)67000 ⏎ 4)66500`
- **NOISE** → got **ENTRY** (tier=library_or_regex) | msg=1478154451079663849 | reply=True
  - content: `Still holding the btc short , I did take partial gains posted above, if btc does drag again 68700-68600 I will set stops at entry <@&697950067285295115>`
  - reply_to: `Avg entry on btc is printing <@&697950067285295115>`

### wrong_direction (1 total)
- **ENTRY** → got **EXIT** (tier=library_or_regex) | msg=1486000121601593457 | reply=True
  - content: `Ibit had a pre-market juicy pump, going to risk some more here and add into our Ibit calls. If you don’t want to risk more you can respectfully go by your risk `
  - reply_to: `Ibit 40c 3/27/26 ⏎  ⏎ @1.00 ⏎  ⏎ <@&697950067285295115>`
