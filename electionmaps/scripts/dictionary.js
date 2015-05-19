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

pollsters["ashcroft"] = "Ashcroft"
pollsters["yougov"] = "Yougov"
pollsters["survation"] = "Survation"
pollsters["tns"] = "TNS BRMB"
pollsters["icm"] = "ICM"
pollsters["populus"] = "Populus"
pollsters["mori"] = "Ipsos Mori"
pollsters["opinium"] = "Opinium"
pollsters["comres"] = "Comres"
pollsters["lucidtalk"] = "Lucidtalk"
pollsters["panelbase"] = "Panelbase"
pollsters["bmg"] = "BMG"


var previousTotalsVoteTotals = {

	"2010" : {
						"eastmidlands": 2180243,
						"eastofengland": 2871212,
						"london": 3348875,
						"northeastengland": 1161554,
						"northernireland": 661055,
						"northwestengland": 3205582,
						"scotland": 2456365,
						"southeastengland": 4274287,
						"southwestengland": 2773443,
						"wales": 1441758,
						"westmidlands": 2640572,
						"yorkshireandthehumber": 2368363
						},

	"2015" : {
						"eastmidlands": 2180243,
						"eastofengland": 2871212,
						"london": 3348875,
						"northeastengland": 1161554,
						"northernireland": 661055,
						"northwestengland": 3205582,
						"scotland": 2456365,
						"southeastengland": 4274287,
						"southwestengland": 2773443,
						"wales": 1441758,
						"westmidlands": 2640572,
						"yorkshireandthehumber": 2368363},

	"2020" : {
				"eastmidlands" : 2230402 ,
				"northeastengland" : 1188183 ,
				"northwestengland" : 3364055 ,
				"southeastengland" : 4394357 ,
				"scotland" : 2910465 ,
				"westmidlands" : 2628943 ,
				"london" : 3536291 ,
				"eastofengland" : 2948622 ,
				"southwestengland" : 2836213 ,
				"wales" : 1498433 ,
				"northernireland" : 718103 ,
				"yorkshireandthehumber" : 2444143}
};

var regions = {
    "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland", "southwestengland", "eastofengland",
                  "eastmidlands", "westmidlands", "london"],
    "scotland"  : ["scotland"],
    "wales" : ["wales"],
    "northernireland" : ["northernireland"]
};

var parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other", "others"];

var previousTotals = {
   "eastmidlands":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"eastmidlands",
      "ukip":78533,
      "sinnfein":0,
      "conservative":915933,
      "labour":663869,
      "green":16462,
      "turnout2010":2236998,
      "snp":0,
      "libdems":463068,
      "dup":0,
      "other":99133,
      "plaidcymru":0
   },
   "northeastengland":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"northeastengland",
      "ukip":34065,
      "sinnfein":0,
      "conservative":282347,
      "labour":518263,
      "green":7674,
      "turnout2010":1195495,
      "snp":0,
      "libdems":280468,
      "dup":0,
      "other":72678,
      "plaidcymru":0
   },
   "northwestengland":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"northwestengland",
      "ukip":104483,
      "sinnfein":0,
      "conservative":1038767,
      "labour":1289934,
      "green":26676,
      "turnout2010":3282612,
      "snp":0,
      "libdems":707774,
      "dup":0,
      "other":114978,
      "plaidcymru":0
   },
   "southeastengland":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"southeastengland",
      "ukip":194064,
      "sinnfein":0,
      "conservative":2116431,
      "labour":693916,
      "green":61854,
      "turnout2010":4293590,
      "snp":0,
      "libdems":1124686,
      "dup":0,
      "other":102639,
      "plaidcymru":0
   },
   "scotland":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"scotland",
      "ukip":24825,
      "sinnfein":0,
      "conservative":412905,
      "labour":1035528,
      "green":11802,
      "turnout2010":2485184,
      "snp":491386,
      "libdems":465471,
      "dup":0,
      "other":43267,
      "plaidcymru":0
   },
   "westmidlands":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"westmidlands",
      "ukip":105606,
      "sinnfein":0,
      "conservative":1044081,
      "labour":808101,
      "green":21923,
      "turnout2010":2647420,
      "snp":0,
      "libdems":540280,
      "dup":0,
      "other":127429,
      "plaidcymru":0
   },
   "london":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"london",
      "ukip":60719,
      "sinnfein":0,
      "conservative":1174477,
      "labour":1245625,
      "green":53385,
      "turnout2010":3401332,
      "snp":0,
      "libdems":751613,
      "dup":0,
      "other":115513,
      "plaidcymru":0
   },
   "eastofengland":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"eastofengland",
      "ukip":109239,
      "sinnfein":0,
      "conservative":1075603,
      "labour":454731,
      "green":34376,
      "turnout2010":2888851,
      "snp":0,
      "libdems":554780,
      "dup":0,
      "other":68853,
      "plaidcymru":0
   },
   "southwestengland":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"southwestengland",
      "ukip":125106,
      "sinnfein":0,
      "conservative":1187637,
      "labour":426910,
      "green":34517,
      "turnout2010":2777574,
      "snp":0,
      "libdems":962954,
      "dup":0,
      "other":40450,
      "plaidcymru":0
   },
   "wales":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"wales",
      "ukip":33894,
      "sinnfein":0,
      "conservative":382730,
      "labour":531602,
      "green":12063,
      "turnout2010":1470665,
      "snp":0,
      "libdems":295164,
      "dup":0,
      "other":49818,
      "plaidcymru":165394
   },
   "northernireland":{
      "alliance":42762,
      "sdlp":110970,
      "uu":102361,
      "region":"northernireland",
      "ukip":0,
      "sinnfein":171942,
      "conservative":0,
      "labour":0,
      "green":0,
      "turnout2010":670329,
      "snp":0,
      "libdems":0,
      "dup":168216,
      "other":74078,
      "plaidcymru":0
   },
   "yorkshireandthehumber":{
      "alliance":0,
      "sdlp":0,
      "uu":0,
      "region":"yorkshireandthehumber",
      "ukip":71144,
      "sinnfein":0,
      "conservative":790062,
      "labour":826537,
      "green":26611,
      "turnout2010":2423926,
      "snp":0,
      "libdems":551738,
      "dup":0,
      "other":157834,
      "plaidcymru":0
   }
}
