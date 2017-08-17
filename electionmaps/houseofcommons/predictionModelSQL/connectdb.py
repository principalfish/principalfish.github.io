#!/usr/bin/python
import mysql.connector as mariadb

db = mariadb.connect(host="localhost", user='root', password='dbpass', database='elections')

cur = db.cursor()
