from yoomoney import Quickpay, Client as YooMoneyClient
import config as cfg

yoomoney_client = YooMoneyClient(cfg.YOOMONEY_ACCESS_TOKEN)

def create_payment_link(amount: int, custom_label: str):
    quickpay = Quickpay(
        receiver="4100118986904668",
        quickpay_form="shop",
        targets="Оплата подписки",
        paymentType="SB",
        sum=amount,
        label=custom_label
    )
    return quickpay.redirected_url

def check_payment_status(label: str) -> bool:
    operations = yoomoney_client.operation_history(label=label)
    for operation in operations.operations:
        if operation.label == label and operation.status == "success":
            return True
    return False