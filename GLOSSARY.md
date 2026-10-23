# Market Monitor

## Language

**Session**:
One instrument's trading day, from 09:15 through 15:30 IST.

**Provider session**:
A session's prices and candles as supplied by one provider; Yahoo and Shoonya histories remain distinct.

**Session transition**:
A change in session state caused by a quote, history, clock advancement, provider selection, or connection quality.

**Native history**:
Historical candles from the selected provider.

**Fallback history**:
Completed Yahoo candles used to fill missing or invalid Shoonya history while retaining their Yahoo provenance.
_Avoid_: Shoonya history, synthetic history

**Session view**:
The displayed session, including its candles, price, opening and provenance; it may combine native and fallback history.
_Avoid_: Provider session

**Source clock**:
The current market time represented by a provider's data, accounting for any publication delay.
_Avoid_: Receipt time
