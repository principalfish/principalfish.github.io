import json

json_data = open("2015info.json").read()
data = json.loads(json_data)

by_elections = {

    "Batley and Spen" : {
            "seat_info" : {
                    'area': 'yorkshireandthehumber',
                    "change" : "hold",
                    "electorate" : 79042,
                    "incumbent" : "labour",
                    "maj" : 16537,
                    "maj_percent" : 81,
                    "percentage_turnout" : 25.8,
                    "turnout" : 20393,
                    "winning_party" : "labour",
                    "by_election" : "20th Oct 2016"

                },

            "party_info" : {
                "labour" : {
                    "name" : "Tracy Brabin",
                    "old" : 21826,
                    "percent" : 85.8,
                    "total" : 17506,
                    "change" : 42.6
                },
                "others" : {
                    "name" : "Others",
                    "old" : 176,
                    "percent" : 14.2,
                    "total" : 2887,
                    "change" : ""
                }
            }
    },

    "Richmond Park" : {
            "seat_info" : {
                    'area': 'london',
                    "change" : "gain",
                    "electorate" : 77251,
                    "incumbent" : "conservative",
                    "maj" : 1872,
                    "maj_percent" : 4.53,
                    "percentage_turnout" : 53.44,
                    "turnout" : 41283,
                    "winning_party" : "libdems",
                    "by_election" : "1st Dec 2016"

                },

            "party_info" : {
                "labour" : {
                    "name" : "Christian Wolmar",
                    "old" : 7296,
                    "percent" : 3.67,
                    "total" : 1515,
                    "change" : -8.67
                },

                "libdems" : {
                    "name" : "Sarah Olney",
                    "old" : 11389,
                    "percent" : 49.68,
                    "total" : 20510,
                    "change" : 30.41
                },

                "other" : {
                    "name" : "Zac Goldsmith",
                    "old" : 34404,
                    "percent" : 45.15,
                    "total" : 18638,
                    "change" : -13.06
                },

                "others" : {
                    "name" : "Others",
                    "old" : 0,
                    "percent" : 1.5,
                    "total" : 620,
                    "change" : ""
                }
            }
    },

    "Witney" : {
            "seat_info" : {
                    'area': 'southeastengland',
                    "change" : "hold",
                    "electorate" : 82169,
                    "incumbent" : "conservative",
                    "maj" : 5702,
                    "maj_percent" : 14.8,
                    "percentage_turnout" : 46.8,
                    "turnout" : 38455,
                    "winning_party" : "conservative",
                    "by_election" : "20th Oct 2016"

                },

            "party_info" : {
                "labour" : {
                    "name" : "Duncan Enright",
                    "old" : 10046,
                    "percent" : 15,
                    "total" : 5765,
                    "change" : -2.2
                },

                "libdems" : {
                    "name" : "Liz Leffman",
                    "old" : 3953,
                    "percent" : 30.2,
                    "total" : 11611,
                    "change" : 23.4
                },

                "conservative" : {
                    "name" : "Robert Courts",
                    "old" : 35201,
                    "percent" : 45,
                    "total" : 17313,
                    "change" : -15.2
                },

                "ukip" : {
                    "name" : "Dickie Bird",
                    "old" : 5352,
                    "percent" : 3.5,
                    "total" : 1354,
                    "change" : -5.7
                },

                "green" : {
                    "name" : "Larry Sanders",
                    "old" : 2970,
                    "percent" : 3.5,
                    "total" : 1363,
                    "change" : -1.6
                },

                "others" : {
                    "name" : "Others",
                    "old" : 959,
                    "percent" : 2.8,
                    "total" : 1049,
                    "change" : ""
                }
            }
    },

    "Sleaford and North Hykeham" : {
            "seat_info" : {
                    "area": "eastmidlands",
                    "change" : "hold",
                    "electorate" : 88660,
                    "incumbent" : "conservative",
                    "maj" : 13144,
                    "maj_percent" : 40.0,
                    "percentage_turnout" : 37.1,
                    "turnout" : 32893,
                    "winning_party" : "conservative",
                    "by_election" : "8th Dec 2016"

                },

            "party_info" : {
                "labour" : {
                    "name" : "Jim Clarke",
                    "old" : 10690,
                    "percent" : 10.2,
                    "total" : 3363,
                    "change" : -7.1
                },

                "libdems" : {
                    "name" : "Ross Pepper",
                    "old" : 3500,
                    "percent" : 11,
                    "total" : 3606,
                    "change" : 5.3
                },

                "conservative" : {
                    "name" : "Caroline Johnson",
                    "old" : 34805,
                    "percent" : 53.5,
                    "total" : 17570,
                    "change" : -2.7
                },

                "ukip" : {
                    "name" : "Victoria Ayling",
                    "old" : 9716,
                    "percent" : 13.5,
                    "total" : 4426,
                    "change" : -2.2
                },


                "others" : {
                    "name" : "Others",
                    "old" : 3239,
                    "percent" : 11.8,
                    "total" : 3869,
                    "change" : ""
                }
            }
    }

}

for seat in by_elections:
    data[seat] = by_elections[seat]



with open("info.json", "w") as fp:
    json.dump(data, fp)
