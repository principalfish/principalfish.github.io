import csv
import math
from datetime import date


seatpolling = {}

parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other"]

class Poll(object):

    def __init__(self, pollster, pollsterweight, date, day, month, year, seat, total, labour, conservative,
                libdems, ukip, green, snp, plaidcymru, other, sdlp, sinnfein, alliance, dup, uu, weight):

            self.pollster = pollster
            self.pollsterweight = pollsterweight
            self.date = date
            self.day = day
            self.month = month
            self.year = year
            self.seat = seat
            self.total = total
            self.labour = labour
            self.conservative = conservative
            self.libdems = libdems
            self.ukip = ukip
            self.green = green
            self.snp = snp
            self.plaidcymru = plaidcymru
            self.other = other
            self.sdlp = sdlp
            self.sinnfein = sinnfein
            self.alliance = alliance
            self.dup = dup
            self.uu = uu
            self.weight = weight



    def weightpolls(self):

        dayofpoll = date(int(self.year), int(self.month), int(self.day))
        daysince =  (today - dayofpoll).days

        self.initialweight = 1


        self.weight = self.initialweight * math.sqrt(float(self.total)/1000) * math.pow(0.99, daysince) # lose 1.5% of value per day since poll


        if self.weight < 0.25:
            self.weight = 0.25


with open("seatpolls.csv", "rb") as csvfile:
    reader = csv.DictReader(csvfile, delimiter ="\t")
    for row in reader:


        info = Poll(pollster = row["pollster"], pollsterweight = row["pollsterweight"], date = row["date"],
                    day = row["day"], month = row["month"], year = row["year"], seat = row["region"], total = row["total"],
                    labour = row["labour"], conservative = row["conservative"], libdems = row["libdems"], ukip = row["ukip"],
                    green = row["green"], snp = row["snp"], plaidcymru = row["plaidcymru"], other = row["other"], sdlp = row["sdlp"],
                    sinnfein = row["sinnfein"], alliance = row["alliance"], dup = row["dup"], uu = row["uu"], weight = row["weight"])

        seatpolling[row["region"]] = info

    csvfile.close()

today = date.today()


# do work on each polls
for poll in seatpolling:
    seatpolling[poll].weightpolls()


# for poll in seatpolling:
#     print "\n"
#     for key, val in vars(seatpolling[poll]).iteritems():
#         print key, val
