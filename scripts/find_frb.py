import pymongo
import urllib
client = pymongo.MongoClient(
    "mongodb://tablename:"+urllib.parse.quote(passw)+"@url/username?ssl=true")  # defaults to port 27017
db = client.huntsman_team
# print the number of documents in a collection
#print(db.huntsman_team.estimated_document_count())


db.huntsman_team.find().pretty()



from mongoschema import Schema
schema = Schema("test", "supplier")




from huntsman.drp.datatable import RawDataTable
dt = RawDataTable()

from huntsman.drp.base import HuntsmanBase
hb = HuntsmanBase()
