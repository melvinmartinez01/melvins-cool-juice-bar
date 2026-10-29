import json, os, time
import boto3
from boto3.dynamodb.conditions import Attr

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
SNS = boto3.client("sns")
TOPIC_ARN = os.environ["TOPIC_ARN"]
BUILT_BY = os.environ["BUILT_BY"]
SHOP = "Melvin's Cool Juice"

def reply(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}

def lambda_handler(event, context):
    route = event.get("routeKey", "")
    try:
        if route == "POST /orders":
            return place_order(event)
        if route == "GET /orders":
            return open_orders()
        if route == "POST /orders/{id}/done":
            return mark_done(event["pathParameters"]["id"])
        return reply(404, {"error": "unknown route"})
    except ValueError as err:
        return reply(400, {"error": str(err)})

# POST /orders - customer places an order
def place_order(event):
    data = json.loads(event.get("body") or "{}")
    name = str(data.get("customerName", "")).strip()[:30]
    items = data.get("items")
    if not name:
        raise ValueError("customerName is required")
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise ValueError("send between 1 and 20 items")
    clean = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("juice", "")).strip():
            raise ValueError("each item needs a juice name")
        clean.append({"juice": str(item["juice"]).strip()[:40], "ice": bool(item.get("ice"))})
    # order numbers 1, 2, 3...
    counter = TABLE.update_item(Key={"orderId": "COUNTER"}, UpdateExpression="ADD lastNumber :one",
                                ExpressionAttributeValues={":one": 1}, ReturnValues="UPDATED_NEW")
    number = int(counter["Attributes"]["lastNumber"])
    TABLE.put_item(Item={"orderId": str(number), "customerName": name, "items": clean,
                         "itemCount": len(clean), "createdAt": int(time.time()), "status": "in_progress"})
    return reply(201, {"orderId": number, "message": f"Order #{number} received by {SHOP}"})

# GET /orders - station tablet asks for open orders
def open_orders():
    kwargs = {"FilterExpression": Attr("status").eq("in_progress")}
    found = []
    while True:
        page = TABLE.scan(**kwargs)
        found += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    orders = [{"orderId": o["orderId"], "customerName": o["customerName"],
               "items": [{"juice": i["juice"], "ice": bool(i["ice"])} for i in o["items"]],
               "itemCount": int(o["itemCount"]), "createdAt": int(o["createdAt"])} for o in found]
    orders.sort(key=lambda o: o["createdAt"])
    return reply(200, {"orders": orders, "builtBy": BUILT_BY})

# POST /orders/{id}/done - station taps an order off, then SNS email
def mark_done(order_id):
    now = int(time.time())
    try:
        result = TABLE.update_item(
            Key={"orderId": order_id},
            ConditionExpression="attribute_exists(orderId) AND #s = :open",
            UpdateExpression="SET #s = :done, completedAt = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":open": "in_progress", ":done": "done", ":now": now},
            ReturnValues="ALL_NEW")
    except TABLE.meta.client.exceptions.ConditionalCheckFailedException:
        return reply(404, {"error": f"order #{order_id} is not open"})
    order = result["Attributes"]
    minutes = (now - int(order["createdAt"])) // 60
    lines = [f"- {i['juice']} ({'ice' if i['ice'] else 'no ice'})" for i in order["items"]]
    SNS.publish(TopicArn=TOPIC_ARN, Subject=f"Order #{order_id} is done - {SHOP}",
                Message=f"Order #{order_id} for {order['customerName']} is done after {minutes} min.\n\n"
                        + "\n".join(lines) + f"\n\n{SHOP} order system - built by {BUILT_BY}")
    return reply(200, {"orderId": order_id, "status": "done"})