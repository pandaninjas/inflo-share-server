from sanic import Sanic, Request, Websocket
from sanic.response import html, json
import os, base64, time, json as json_module
import redis.asyncio as redis
from typing import TYPE_CHECKING

app = Sanic("Inflo-Sharing-Server")
app.ctx.db: redis.Redis = redis.from_url(os.environ.get("INFLO_REDIS_URL", "redis://localhost"))


@app.get("/<id>")
async def main(_, id):
    return html(open("index.html", "r").read())


@app.post("/start")
async def start(_):
    secret = base64.urlsafe_b64encode(os.urandom(16)).decode("utf-8")
    if TYPE_CHECKING:
        assert isinstance(app.ctx.db, redis.Redis)

    while await app.ctx.db.get("SECRET$" + secret) != None:
        secret = base64.urlsafe_b64encode(os.urandom(16)).decode("utf-8")

    connection_id = base64.urlsafe_b64encode(os.urandom(16)).decode("utf-8")

    while await app.ctx.db.get("ID$" + connection_id) != None:
        connection_id = base64.urlsafe_b64encode(os.urandom(16)).decode("utf-8")

    await app.ctx.db.set(
        "SECRET$" + secret, "ID$" + connection_id, ex=60 * 60 * 12
    )  # 12 hours
    await app.ctx.db.hmset("ID$" + connection_id, {"playing": 1, "id": ""})
    await app.ctx.db.expire("ID$" + connection_id, 24 * 60 * 60)

    return json({"secret": secret, "id": connection_id})


@app.put("/update/<secret:str>")
async def record_event(request: Request, secret: str):
    if TYPE_CHECKING:
        assert isinstance(app.ctx.db, redis.Redis)

    if (
        len(secret) != 24
        or not isinstance(request.json, dict)
        or not isinstance(request.json["playing"], bool)
        or not isinstance(request.json["id"], str)
        or len(request.json["id"]) != 11
        or not isinstance(request.json["progress"], (int, float))
    ):
        return json({"status": "fail"})

    connection_id: str = (await app.ctx.db.get("SECRET$" + secret)).decode("utf-8")
    event = {
        "playing": 1 if request.json["playing"] else 0,
        "id": request.json["id"],
        "time": time.time(),
        "progress": request.json["progress"],
    }
    await app.ctx.db.publish(connection_id, json_module.dumps(event))
    await app.ctx.db.hmset(connection_id, event)
    return json({"status": "ok"})


@app.websocket("/feed/<connection_id:str>")
async def feed(request: Request, ws: Websocket, connection_id: str):
    if TYPE_CHECKING:
        assert isinstance(app.ctx.db, redis.Redis)

    if len(connection_id) != 24:
        return json({"status": "fail"})

    data = await app.ctx.db.hgetall("ID$" + connection_id)
    await app.ctx.db.expire("ID$" + connection_id, 24 * 60 * 60)
    data = {k.decode(): v.decode() for k, v in data.items()}
    print(data)
    data = {
        "type": 0,
        "playing": data["playing"] == "1",
        "id": data["id"],
        "progress": time.time() - float(data["time"]) + float(data["progress"]),
    }
    await ws.send(json_module.dumps(data))

    async with app.ctx.db.pubsub() as pubsub:
        await pubsub.subscribe("ID$" + connection_id)
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=None
            )
            if not message:
                continue
            message_data = json_module.loads(message["data"])
            print(message_data)
            message_data = {
                "playing": message_data["playing"] == 1,
                "id": message_data["id"],
                "progress": message_data["progress"]
            }

            if message_data["id"] != data["id"]:
                message_data["type"] = 0
                await ws.send(json_module.dumps(message_data))
            elif message_data["playing"] != data["playing"]:
                await ws.send(
                    json_module.dumps(
                        {"type": 1, "playing": message_data["playing"]}
                    )
                )
            elif message_data["progress"] != data["progress"]:
                await ws.send(
                    json_module.dumps({"type": 2, "seek": message_data["progress"]})
                )

            data = message_data
