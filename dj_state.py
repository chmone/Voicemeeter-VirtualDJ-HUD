"""VirtualDJ commands observed by the HUD, with unknown initial knob positions."""
from dj_setup import filter_position


class DeckEstimates:
    def __init__(self):
        self.deck = None
        self.values = {}
        self.filter_enabled = {}
        self.sync_deck = None

    def apply(self, action, options, *, coarse=False):
        if action in ('deck1', 'deck2'):
            self.deck = int(action[-1])
        elif action == 'reset_all':
            self.deck = 1
            self.sync_deck = None
            for deck in (1, 2):
                self.reset_eq(deck)
                self.values[deck, 'filter'] = 50
                self.filter_enabled[deck] = True
                self.values[deck, 'level'] = 100
        elif self.deck is None:
            return
        elif action == 'sync':
            self.sync_deck = self.deck
        elif action == 'eq_reset':
            self.reset_eq(self.deck)
        elif action == 'bass_swap':
            self.values[3 - self.deck, 'eq_low'] = 0
            self.values[self.deck, 'eq_low'] = 50
        elif action == 'filter_toggle':
            if self.deck in self.filter_enabled:
                self.filter_enabled[self.deck] = not self.filter_enabled[self.deck]
        elif action == 'filter_center':
            self.values[self.deck, 'filter'] = 50
        elif action != 'none':
            control, direction = action.split('_')
            eq_step = options.eq_coarse_step if coarse else options.eq_step
            name, step = {
                'high': ('eq_high', eq_step), 'mid': ('eq_mid', eq_step),
                'low': ('eq_low', eq_step), 'filter': ('filter', options.filter_step),
                'volume': ('level', options.volume_step),
            }[control]
            key = self.deck, name
            current = self.values.get(key)
            if current is not None:
                delta = step if direction == 'up' else -step
                self.values[key] = (filter_position(current, delta) if name == 'filter'
                                    else min(100, max(0, current + delta)))

    def reset_eq(self, deck):
        for name in ('eq_high', 'eq_mid', 'eq_low'):
            self.values[deck, name] = 50
