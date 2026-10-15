"""Local QA preview using sample data in an explicitly disposable database.

DATABASE_URL=postgresql:///codex_procurement_qa_preview \
    python -m dashboard.procurement.preview --reset

Never imported by the production app. No external orders or messages are sent.
"""

import argparse
import os
import secrets
from datetime import date, datetime, timedelta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Reset procurement sample data in the disposable database")
    parser.add_argument("--port", type=int, default=5056)
    args = parser.parse_args()
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.schema import CreateSchema
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        parser.error("Explicitly set DATABASE_URL to a local codex_procurement_qa_ database.")
    parsed = make_url(url)
    if not parsed.database.startswith("codex_procurement_qa_") or parsed.host not in (None, "localhost", "127.0.0.1"):
        parser.error("Preview requires a disposable local database named codex_procurement_qa_*.")
    from dashboard.app import create_app
    from dashboard.auth.models import Base as AuthBase, User, UserFeaturePermission, get_session
    from database.models import Listing, ListingTag, Tag, get_session as get_main_session
    from .models import Base, CatalogItem, Location, Staff, Ticket
    from .service import ProcurementService
    engine = create_engine(url)
    with engine.begin() as connection:
        for schema in {t.schema for t in AuthBase.metadata.tables.values() if t.schema}:
            connection.execute(CreateSchema(schema, if_not_exists=True))
    app = create_app()
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.config["PROCUREMENT_QA_PREVIEW"] = True
    if args.reset:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE " + ", ".join(f"procurement.{t.name}" for t in Base.metadata.sorted_tables) + " RESTART IDENTITY CASCADE"))
    with get_session() as db:
        owner = db.scalar(select(User).where(User.role == "owner"))
        owner.name = "Procurement QA"
        owner_id = owner.user_id
        cleaner = db.scalar(select(User).where(User.email == "procurement-preview-cleaner@example.invalid"))
        if not cleaner:
            cleaner = User(email="procurement-preview-cleaner@example.invalid", name="Maria · QA housekeeper", role="user", is_approved=True)
            db.add(cleaner)
            db.flush()
            from dashboard.auth.features import FEATURES
            for feature in FEATURES:
                db.add(UserFeaturePermission(user_id=cleaner.user_id, feature_key=feature, is_enabled=feature == "procurement"))
        cleaner_id = cleaner.user_id
        db.commit()
    properties = [
        {"id":101,"name":"Searenity Haven","city":"Seattle","timezone":"America/Los_Angeles","tag":"waterfront"},
        {"id":102,"name":"Appleseed Cabin","city":"Hocking Hills","timezone":"America/New_York","tag":"ohio cabins"},
        {"id":103,"name":"Atlanta · PT300","city":"Atlanta","timezone":"America/New_York","tag":"atlanta"},
        {"id":104,"name":"Cedar & Stone Retreat","city":"Whidbey Island","timezone":"America/Los_Angeles","tag":"whidbey island"},
    ]
    with get_main_session(None) as db:
        for p in properties:
            listing = db.get(Listing, p["id"])
            if not listing:
                listing = Listing(listing_id=p["id"])
                db.add(listing)
            listing.name, listing.internal_listing_name, listing.city, listing.timezone_name = p["name"], p["name"], p["city"], p["timezone"]
            tag = db.scalar(select(Tag).where(Tag.name == p["tag"]))
            if not tag:
                tag = Tag(name=p["tag"])
                db.add(tag)
                db.flush()
            if not db.get(ListingTag, (p["id"], tag.tag_id)):
                db.add(ListingTag(listing_id=p["id"],tag_id=tag.tag_id))
        db.commit()
    with get_session() as db:
        if not db.scalar(select(Ticket.id).limit(1)):
            maria = Staff(name="Maria Lopez", phone="+12025550101", user_id=cleaner_id, listing_ids=[101,104], active=True)
            anna = Staff(name="Anna Parker", phone="+12025550102", listing_ids=[102], active=True)
            jordan = Staff(name="Jordan Williams", phone="+12025550103", listing_ids=[103], active=True)
            db.add_all([maria,anna,jordan]); db.flush()
            location = Location(label="Maria · supply storage",kind="housekeeper",address="100 Example Lane\nSeattle, WA 98101\nUnited States — QA address",staff_id=maria.id,active=True)
            storage = Location(label="Atlanta · shared supply room",kind="storage",address="200 Example Street\nAtlanta, GA 30303\nUnited States — QA address",active=True)
            db.add_all([location,storage]); db.flush()
            products = [("Toilet paper","paper products","rolls","24 rolls per pack"),("Paper towels","paper products","rolls","12 rolls per pack"),("Laundry detergent","cleaning","bottles","100-load bottle"),("Trash bags","cleaning","bags","13 gallon, 80 per box"),("Shampoo","guest toiletries","bottles","Refill bottle, 1 litre"),("Dishwasher pods","cleaning","pods","100 pods per tub")]
            catalog = []
            for name,category,unit,pack in products:
                item = CatalogItem(name=name,category=category,unit=unit,pack_description=pack,active=True); db.add(item); catalog.append(item)
            db.flush()
            prop_map = {p["id"]:{**p,"tags":[p["tag"]]} for p in properties}
            service = ProcurementService(db,actor="Procurement QA",user_id=owner_id,can_approve=True,properties=prop_map,users={owner_id:{"id":owner_id,"name":"Procurement QA"}})
            def ticket(title, prop, staff, items, priority="normal", hours=72):
                result = service.create_ticket({"title":title,"listing_id":prop,"requester_staff_id":staff.id,"owner_user_id":owner_id,"priority":priority,"required_by":(datetime.now().astimezone()+timedelta(hours=hours)).isoformat(),"items":[{"catalog_id":catalog[index].id,"quantity":qty} for index,qty in items],"source":{"channel":"whatsapp:qa-group","message_id":f"qa-message-{prop}-{title}","body":f"Hi Ethan, please help restock {title.lower()}. Supplies are needed before the next guest arrives."}})[0]
                return result
            first = ticket("Weekly guest supply restock",101,maria,[(0,"24"),(1,"12"),(2,"1"),(3,"80")])
            urgent = ticket("Guest toiletries running low",103,jordan,[(4,"3"),(3,"80")],"critical",-2)
            cabin = ticket("Cleaning supplies for weekend arrivals",102,anna,[(2,"2"),(5,"100")],"high",28)
            received = ticket("Paper goods replenishment",104,maria,[(0,"24"),(1,"12")],"normal",48)
            planned = ticket("Monthly bulk replenishment",103,jordan,[(0,"48"),(3,"160")],"planned",240)
            unknown = ticket("More detergent needed",102,anna,[(2,None)],"normal",96)
            def order_for(t, prices, draft=False, partial=False):
                order = service.save_order({"vendor":"Walmart" if t.listing_id != 102 else "Amazon","currency":"USD","location_id":location.id if t.listing_id in (101,104) else storage.id,"tax":"5.68","shipping":"0.00","responsibility":"owner","lines":[{"catalog_id":i.catalog_id,"quantity":str(i.quantity),"unit_price":str(price),"allocations":[{"item_id":i.id,"quantity":str(i.quantity)}]} for i,price in zip(t.items,prices)]})
                if not draft:
                    service.order_action(order.id,"approve",{"version":order.version})
                    service.order_action(order.id,"place",{"version":order.version,"reference":f"QA-ORDER-{order.id:04d}","purchased_on":date.today().isoformat()})
                    service.create_shipment(order.id,{"version":order.version,"carrier":"Retailer delivery","tracking":f"QA-TRACK-{order.id:04d}","expected_at":(datetime.now().astimezone()+timedelta(hours=12)).isoformat(),"lines":[{"line_id":line.id,"quantity":str(line.quantity)} for line in order.lines]})
                return order
            order_for(first,[".75","1.49","18.50",".18"],draft=True)
            order_for(cabin,["18.50",".19"])
            arrived = order_for(received,[".75","1.49"])
            service.deliver_shipment(arrived.shipments[0].id,{"version":arrived.version})
            db.commit()
    from flask import request, session
    @app.before_request
    def preview_identity():
        if request.args.get("as") in ("housekeeper","manager"):
            session["qa_as"] = request.args["as"]
        session["logged_in"] = True
        session["user_id"] = cleaner_id if session.get("qa_as") == "housekeeper" else owner_id
    print(f"QA preview (sample data): http://127.0.0.1:{args.port}/procurement/")
    app.run(host="127.0.0.1",port=args.port,debug=False,use_reloader=False)


if __name__ == "__main__":
    main()
