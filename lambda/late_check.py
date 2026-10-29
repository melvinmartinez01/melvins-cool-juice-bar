import json, os, time
import boto3
from boto3.dynamodb.conditions import Attr

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
CLOUDWATCH = boto3.client("cloudwatch")

# 4 items or fewer: late after 10 min. 5 or more: late after 20 min.
def time_limit(item_count):
    return 10 * 60 if item_count <= 4 else 20 * 60

def lambda_handler(event, context):
    now = int(time.time())
    kwargs = {"FilterExpression": Attr("status").eq("in_progress")}
    late = []
    while True:
        page = TABLE.scan(**kwargs)
        for order in page["Items"]:
            waited = now - int(order["createdAt"])
            if waited > time_limit(int(order["itemCount"])):
                late.append({"orderId": order["orderId"], "waitedMin": waited // 60})
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    # send the count to CloudWatch - the alarm watches this number
    CLOUDWATCH.put_metric_data(
        Namespace="MelvinsCoolJuice",
        MetricData=[{"MetricName": "LateOrders", "Value": len(late), "Unit": "Count"}])
    print(json.dumps({"lateOrders": len(late), "late": late}))
    return {"lateOrders": len(late)}