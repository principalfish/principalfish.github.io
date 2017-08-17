#!/usr/bin/python
import mysql.connector as mariadb
import json

db = mariadb.connect(host="localhost", user='root', password='dbpass', database='elections')

cur = db.cursor()

with open ("../2010election.json") as seat_file:
    seat_data = json.load(seat_file)

# creating table of seats


vars = {

}



db.close()
