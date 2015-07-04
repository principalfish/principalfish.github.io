var partylist = {};

partylist["labour"] = "Labour";
partylist["conservative"] = "Conservative";
partylist["libdems"] = "Liberal Democrats";
partylist["snp"] = "SNP";
partylist["ukip"] = "UKIP";
partylist["plaidcymru"] = "Plaid Cymru";
partylist["green"] = "Green";
partylist["respect"] = "Respect";
partylist["speaker"] = "Speaker";
partylist["independent"] = "Independent";
partylist["uu"] = "UUP";
partylist["sinnfein"] = "Sinn Féin";
partylist["sdlp"] = "SDLP";
partylist["dup"] = "DUP";
partylist["alliance"] = "Alliance";
partylist["other1"] = "Other";
partylist["other2"] = "Others";
partylist["other"] = "Other";
partylist["others"] = "Others";
partylist["total"] = "Total";


var regionlist = {};

regionlist["london"] = "London";
regionlist["southeastengland"] = "S.E. England";
regionlist["southwestengland"] = "S. W. England";
regionlist["westmidlands"] = "W. Midlands";
regionlist["northwestengland"] = "N. W. England";
regionlist["northeastengland"] = "N. E. England";
regionlist["yorkshireandthehumber"] = "Yorks & Humber";
regionlist["eastmidlands"] = "E. Midlands";
regionlist["eastofengland"] = "E. England";
regionlist["scotland"] = "Scotland";
regionlist["wales"] = "Wales";
regionlist["northernireland"] = "N. Ireland";


var pollsters = {};

pollsters["ashcroft"] = "Ashcroft";
pollsters["yougov"] = "Yougov";
pollsters["survation"] = "Survation";
pollsters["tns"] = "TNS BRMB";
pollsters["icm"] = "ICM";
pollsters["populus"] = "Populus";
pollsters["mori"] = "Ipsos Mori";
pollsters["opinium"] = "Opinium";
pollsters["comres"] = "Comres";
pollsters["lucidtalk"] = "Lucidtalk";
pollsters["panelbase"] = "Panelbase";
pollsters["bmg"] = "BMG";


var previousTotalsByYearByParty = {

  	"2010" :{'eastmidlands': {'alliance': 0,
                            'conservative': 887467,
                            'dup': 0,
                            'green': 13102,
                            'labour': 651841,
                            'libdems': 442784,
                            'other': 99151,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2180460,
                            'ukip': 86115,
                            'uu': 0},
                           'eastofengland': {'alliance': 0,
                            'conservative': 1342522,
                            'dup': 0,
                            'green': 42830,
                            'labour': 557739,
                            'libdems': 687838,
                            'other': 83619,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2859038,
                            'ukip': 144490,
                            'uu': 0},
                           'london': {'alliance': 0,
                            'conservative': 1156068,
                            'dup': 0,
                            'green': 54349,
                            'labour': 1224041,
                            'libdems': 739932,
                            'other': 90628,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 3325263,
                            'ukip': 60245,
                            'uu': 0},
                           'northeastengland': {'alliance': 0,
                            'conservative': 272098,
                            'dup': 0,
                            'green': 3787,
                            'labour': 506611,
                            'libdems': 270641,
                            'other': 69479,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 1161554,
                            'ukip': 38938,
                            'uu': 0},
                           'northernireland': {'alliance': 42378,
                            'conservative': 0,
                            'dup': 161297,
                            'green': 3542,
                            'labour': 0,
                            'libdems': 0,
                            'other': 8772,
                            'plaidcymru': 0,
                            'sdlp': 109449,
                            'sinnfein': 161536,
                            'snp': 0,
                            'turnout': 584212,
                            'ukip': 0,
                            'uu': 97238},
                           'northwestengland': {'alliance': 0,
                            'conservative': 1011560,
                            'dup': 0,
                            'green': 18931,
                            'labour': 1269322,
                            'libdems': 678854,
                            'other': 106561,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 3202158,
                            'ukip': 116930,
                            'uu': 0},
                           'scotland': {'alliance': 0,
                            'conservative': 411187,
                            'dup': 0,
                            'green': 0,
                            'labour': 1029653,
                            'libdems': 461091,
                            'other': 43267,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 494089,
                            'turnout': 2456365,
                            'ukip': 17078,
                            'uu': 0},
                           'southeastengland': {'alliance': 0,
                            'conservative': 2120695,
                            'dup': 0,
                            'green': 63082,
                            'labour': 689564,
                            'libdems': 1105711,
                            'other': 81439,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 4264256,
                            'ukip': 203765,
                            'uu': 0},
                           'southwestengland': {'alliance': 0,
                            'conservative': 1187637,
                            'dup': 0,
                            'green': 31517,
                            'labour': 426910,
                            'libdems': 962954,
                            'other': 40450,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2773443,
                            'ukip': 123975,
                            'uu': 0},
                           'wales': {'alliance': 0,
                            'conservative': 374036,
                            'dup': 0,
                            'green': 6539,
                            'labour': 523533,
                            'libdems': 287392,
                            'other': 42679,
                            'plaidcymru': 165397,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 1435300,
                            'ukip': 35724,
                            'uu': 0},
                           'westmidlands': {'alliance': 0,
                            'conservative': 1044081,
                            'dup': 0,
                            'green': 14996,
                            'labour': 808101,
                            'libdems': 540280,
                            'other': 99039,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2612182,
                            'ukip': 105685,
                            'uu': 0},
                           'yorkshireandthehumber': {'alliance': 0,
                            'conservative': 770659,
                            'dup': 0,
                            'green': 20365,
                            'labour': 806799,
                            'libdems': 537586,
                            'other': 134549,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2342263,
                            'ukip': 72305,
                            'uu': 0}},

  	"2015" : {'eastmidlands': {'alliance': 0,
                            'conservative': 969379,
                            'dup': 0,
                            'green': 66239,
                            'labour': 705767,
                            'libdems': 124039,
                            'other': 13201,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2230402,
                            'ukip': 351777,
                            'uu': 0},
             'eastofengland': {'alliance': 0,
                            'conservative': 1445946,
                            'dup': 0,
                            'green': 116274,
                            'labour': 649320,
                            'libdems': 243191,
                            'other': 15374,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2948622,
                            'ukip': 478517,
                            'uu': 0},
                           'london': {'alliance': 0,
                            'conservative': 1233378,
                            'dup': 0,
                            'green': 171652,
                            'labour': 1545110,
                            'libdems': 272544,
                            'other': 26626,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 3536291,
                            'ukip': 286981,
                            'uu': 0},
                           'northeastengland': {'alliance': 0,
                            'conservative': 300883,
                            'dup': 0,
                            'green': 43051,
                            'labour': 557100,
                            'libdems': 77125,
                            'other': 11201,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 1188183,
                            'ukip': 198823,
                            'uu': 0},
                           'northernireland': {'alliance': 61556,
                            'conservative': 9055,
                            'dup': 184260,
                            'green': 6822,
                            'labour': 0,
                            'libdems': 0,
                            'other': 29421,
                            'plaidcymru': 0,
                            'sdlp': 99809,
                            'sinnfein': 176232,
                            'snp': 0,
                            'turnout': 700414,
                            'ukip': 18324,
                            'uu': 114935},
                           'northwestengland': {'alliance': 0,
                            'conservative': 1050124,
                            'dup': 0,
                            'green': 107889,
                            'labour': 1502047,
                            'libdems': 219998,
                            'other': 24926,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 3364055,
                            'ukip': 459071,
                            'uu': 0},
                           'scotland': {'alliance': 0,
                            'conservative': 434097,
                            'dup': 0,
                            'green': 39205,
                            'labour': 707147,
                            'libdems': 219675,
                            'other': 8827,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 1454436,
                            'turnout': 2910465,
                            'ukip': 47078,
                            'uu': 0},
                           'southeastengland': {'alliance': 0,
                            'conservative': 2234356,
                            'dup': 0,
                            'green': 227883,
                            'labour': 804774,
                            'libdems': 413586,
                            'other': 32182,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 4359740,
                            'ukip': 646959,
                            'uu': 0},
                           'southwestengland': {'alliance': 0,
                            'conservative': 1319967,
                            'dup': 0,
                            'green': 168130,
                            'labour': 501684,
                            'libdems': 428873,
                            'other': 19873,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2823073,
                            'ukip': 384546,
                            'uu': 0},
                           'wales': {'alliance': 0,
                            'conservative': 408213,
                            'dup': 0,
                            'green': 38344,
                            'labour': 552473,
                            'libdems': 97783,
                            'other': 15566,
                            'plaidcymru': 181694,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 1498433,
                            'ukip': 204360,
                            'uu': 0},
                           'westmidlands': {'alliance': 0,
                            'conservative': 1098113,
                            'dup': 0,
                            'green': 85653,
                            'labour': 865067,
                            'libdems': 145009,
                            'other': 22331,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2628943,
                            'ukip': 412770,
                            'uu': 0},
                           'yorkshireandthehumber': {'alliance': 0,
                            'conservative': 796792,
                            'dup': 0,
                            'green': 86471,
                            'labour': 956837,
                            'libdems': 174065,
                            'other': 38055,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 2444143,
                            'ukip': 391923,
                            'uu': 0},
                          'england': {'alliance': 0,
                            'conservative': 10448938,
                            'dup': 0,
                            'green': 1073242,
                            'labour': 8087706,
                            'libdems': 2098430,
                            'other': 203769,
                            'plaidcymru': 0,
                            'sdlp': 0,
                            'sinnfein': 0,
                            'snp': 0,
                            'turnout': 25523452,
                            'ukip': 3611367,
                            'uu': 0}
                            }

};

previousTotalsByYearByParty["2005"] = previousTotalsByYearByParty["2010"];

var regions = {
    "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland", "southwestengland", "eastofengland",
                  "eastmidlands", "westmidlands", "london"],
    "scotland"  : ["scotland"],
    "wales" : ["wales"],
    "northernireland" : ["northernireland"]
};

var parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other", "others"];