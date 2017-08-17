import connectdb, sqlfuncs

import csv

poll_table_columns = [
    "id INTEGER PRIMARY KEY AUTO_INCREMENT",
    "code INTEGER",
    "company TEXT",
    "date DATE",
    "region TEXT",
    "total INTEGER",
    "conservative INTEGER",
    "labour INTEGER",
    "libdems INTEGER",
    "ukip INTEGER",
    "green INTEGER",
    "snp INTEGER",
    "plaidcymru INTEGER",
    "other INTEGER",
    "sdlp INTEGER",
    "sinnfein INTEGER",
    "alliance INTEGER",
    "dup INTEGER",
    "uu INTEGER"
]

sqlfuncs.create_table("polls", poll_table_columns)

with open("polls2015-2017.csv") as csv_file:
    election2017_polls = csv.DictReader(csv_file, delimiter = "\t")

    for row in election2017_polls:
        #print row

        columns = []
        values = []

        for key, val in row.iteritems():
            if key not in ["day", "month", "year"]:
                if val == "":
                    val = "0"
                if key in ["company", "region"]:
                    val = "'%s'" % val
                columns.append(key)
                values.append(val)

        date = "'" + row["year"] + "-" + row["month"].zfill(2) + "-" + row["day"].zfill(2) + "'"

        columns.append("date")
        values.append(date)

        columns = ", ".join(columns)
        values = ", ".join(values)

        sqlfuncs.insert_into("polls", columns, values)
