from aiogram.fsm.state import State, StatesGroup

class SubscriptionStates(StatesGroup):
    wait_for_accept = State()
    choosing_buy_period = State()
    awaiting_payment = State()
    choosing_extend_period = State()
    awaiting_extend_payment = State()
    selecting_manual = State()
    extending_subscription = State()