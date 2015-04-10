//for use purely in user input calculations
previousTotals = {};
previousPercentages = {};

// get vote totals for dual purpose of displaying on page on user select and use in user input calculation
function getVoteTotals(data, region) {

	if (region == "/election2015/data/previoustotals.csv"){
		$.each(data, function(i){
			var info = {};
			info["region"] = data[i].region;
			info["turnout2010"] = data[i].turnout2010;
			info["conservative"] = data[i].conservative;
			info["labour"] = data[i].labour;
			info["libdems"] = data[i].libdems;
			info["ukip"] = data[i].ukip;
			info["snp"] = data[i].snp;
			info["plaidcymru"] = data[i].plaidcymru;
			info["green"] = data[i].green;
			info["uu"] = data[i].uu;
			info["sdlp"] = data[i].sdlp;
			info["dup"] = data[i].dup;
			info["sinnfein"] = data[i].sinnfein;
			info["alliance"] = data[i].alliance;
			info["other"] = data[i].other;

			previousTotals[data[i].region] = info;
		});

		$.each(data, function(i){
			var info = {};

			info["conservative"] = 100 *  data[i].conservative / parseFloat(data[i].turnout2010);
			info["labour"] = 100 *  data[i].labour / parseFloat(data[i].turnout2010);
			info["libdems"] = 100 *  data[i].libdems / parseFloat(data[i].turnout2010);
			info["ukip"] = 100 *  data[i].ukip / parseFloat(data[i].turnout2010);
			info["snp"] = 100 *  data[i].snp / parseFloat(data[i].turnout2010);
			info["plaidcymru"] = 100 *  data[i].plaidcymru / parseFloat(data[i].turnout2010);
			info["green"] = 100 *  data[i].green / parseFloat(data[i].turnout2010);
			info["uu"] = 100 *  data[i].uu / parseFloat(data[i].turnout2010);
			info["sdlp"] = 100 *  data[i].sdlp / parseFloat(data[i].turnout2010);
			info["dup"] = 100 *  data[i].dup / parseFloat(data[i].turnout2010);
			info["sinnfein"] = 100 *  data[i].sinnfein / parseFloat(data[i].turnout2010);
			info["alliance"] = 100 *  data[i].alliance / parseFloat(data[i].turnout2010);
			info["other"] = 100 *  data[i].other / parseFloat(data[i].turnout2010);

			previousPercentages[data[i].region] = info;
		});
	}

	else
		region = region.slice(27)

		$.each(data, function(i){
			var info = {};
			info["code"] = data[i].code;
			info["seats"] = data[i].seats;
			info["change"] = data[i].change;
			info["votepercent"] = data[i].votepercent;
			info["votepercentchange"] = data[i].votepercentchange;


		if (region == "greatbritain.csv")
			greatbritainVoteTotals.push(info)

		if (region == "england.csv")
			englandVoteTotals.push(info)

		if (region == "scotland.csv")
			scotlandVoteTotals.push(info)

		if (region == "wales.csv")
			walesVoteTotals.push(info)

		if (region == "northernireland.csv")
			northernirelandVoteTotals.push(info)

		if (region == "eastofengland.csv")
			eastofenglandVoteTotals.push(info)

		if (region == "northeastengland.csv")
			northeastenglandVoteTotals.push(info)

		if (region == "northwestengland.csv")
			northwestenglandVoteTotals.push(info)

		if (region == "southeastengland.csv")
			southeastenglandVoteTotals.push(info)

		if (region == "southwestengland.csv")
			southwestenglandVoteTotals.push(info)

		if (region == "london.csv")
			londonVoteTotals.push(info)

		if (region == "eastmidlands.csv")
			eastmidlandsVoteTotals.push(info)

		if (region == "westmidlands.csv")
			westmidlandsVoteTotals.push(info)

		if (region == "yorkshireandthehumber.csv")
			yorkshireandthehumberVoteTotals.push(info)
		});
}

// used when resetting userinputs
function getSeatInfoAgain(data){

	$.each(data, function(i){
		seatData[data[i].seat] = data[i];
	});

	alterMap("reset");
}

// get complete 2010 seat Data for use in user input calculations
var oldSeatData = {};

function getOldSeatInfo(data){
	$.each(data, function(i){
		data[i].labour /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].conservative /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].libdems /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].ukip /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].green /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].other /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].plaidcymru /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].snp /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].sdlp /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].dup /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].sinnfein /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].alliance /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].uu /= (parseFloat(data[i].turnout2010) / 100 )

		oldSeatData[data[i].seat] = data[i];
	});
}

function getMapInfo(){
	parseData("/election2015/data/info.csv", getSeatInfo);

	parseData("/election2015/data/previoustotals.csv", getVoteTotals);

	parseData("/election2015/data/oldinfo.csv", getOldSeatInfo);
}

// get vote totals for initial display and possible future display
function getInfoFromFiles(){

	parseData("/election2015/data/regions/projectionvotetotals.csv", getVoteTotalsInitial);

	// get rest of vote totals for later

	parseData("/election2015/data/regions/greatbritain.csv", getVoteTotals);
	parseData("/election2015/data/regions/england.csv", getVoteTotals);
	parseData("/election2015/data/regions/scotland.csv", getVoteTotals);
	parseData("/election2015/data/regions/wales.csv", getVoteTotals);
	parseData("/election2015/data/regions/northernireland.csv", getVoteTotals);
	parseData("/election2015/data/regions/northeastengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/northwestengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/southeastengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/southwestengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/london.csv", getVoteTotals);
	parseData("/election2015/data/regions/eastmidlands.csv", getVoteTotals);
	parseData("/election2015/data/regions/westmidlands.csv", getVoteTotals);
	parseData("/election2015/data/regions/eastofengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/yorkshireandthehumber.csv", getVoteTotals);

};

//initiate data accrual + map load
getMapInfo();
getInfoFromFiles();

//collate vote totals


// USER INPUT

// func to check user input percentages dont add up to more than 100 and to work out other percentage

function userInputCheck(inputform, country){


  var otherPercentage = sumFormPercentages(inputform).toFixed(0);

  spanId = "#other" + country


	if (otherPercentage < 0) {
		$(spanId).html("< 0");
	}

	else {
		$(spanId).html(otherPercentage);
	}
};


function sumFormPercentages(inputform){

  var $form = $(inputform);

  var $sumpercentages = $form.find(".inputnumbers");

  var sum = 0
  $sumpercentages.each(function() {
    var value = Number($(this).val());
    if (isNaN(value)) {
      value = 0;
    }
    sum += value;
  });

	console.log(sum)

	var otherPercentage = 100 - sum
	return otherPercentage
}

englandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
scotlandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0, "snp" : 0 };
walesUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "plaidcymru" : 0} ;
northernirelandUserNumbers = {"dup" : 0, "sinnfein": 0, "sdlp": 0, "uu": 0, "alliance" : 0} ;


// CONVERSION OF BACK-END PYTHON SCRIPT TO JAVASCRIPT FOR USE IN BROWSER
// simplified
var regions = {
    "england"  : ["northeastengland", "northwestengland", "yorkshireandthehumber", "southeastengland", "southwestengland", "eastofengland",
                  "eastmidlands", "westmidlands", "london"],
    "scotland"  : ["scotland"],
    "wales" : ["wales"],
    "northernireland" : ["northernireland"]
};

parties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green", "uu", "sdlp", "dup", "sinnfein", "alliance", "other"]

// if user input is NaN set to 0
function analyseUserInput(inputform, region){

  var otherPercentage = sumFormPercentages(inputform);

  if (otherPercentage < 0){
    alert("Percentages add up to more than 100, try again.");
  }

  else {

    var userRegionalTotals = {};

    if (region == "england"){
      $.each(englandUserNumbers, function(party){
        if (isNaN(englandUserNumbers[party])){
          englandUserNumbers[party] = 0;
        }
      });
      englandUserNumbers["other"] = 100 - englandUserNumbers["labour"] - englandUserNumbers["conservative"] - englandUserNumbers["libdems"] - englandUserNumbers["ukip"] - englandUserNumbers["green"];
      var percentages = englandUserNumbers;
    }

    if (region == "scotland"){
      $.each(scotlandUserNumbers, function(party){
        if (isNaN(scotlandUserNumbers[party])){
          scotlandUserNumbers[party] = 0;
        }
      });
      scotlandUserNumbers["other"] = 100 - scotlandUserNumbers["labour"] - scotlandUserNumbers["conservative"] - scotlandUserNumbers["libdems"] - scotlandUserNumbers["ukip"] - scotlandUserNumbers["green"] - scotlandUserNumbers["snp"];
      var percentages = scotlandUserNumbers;
    }

    if (region == "wales"){
      $.each(walesUserNumbers, function(party){
        if (isNaN(walesUserNumbers[party])){
          walesUserNumbers[party] = 0;
        }
      });
      walesUserNumbers["other"] = 100 - walesUserNumbers["labour"] - walesUserNumbers["conservative"] - walesUserNumbers["libdems"] - walesUserNumbers["ukip"] - walesUserNumbers["green"] - walesUserNumbers["plaidcymru"];

      var percentages = walesUserNumbers;
    }

    if (region == "northernireland"){
      $.each(northernirelandUserNumbers, function(party){
        if (isNaN(northernirelandUserNumbers[party])){
          northernirelandUserNumbers[party] = 0;
        }
      });
      northernirelandUserNumbers["other"] = 100 - northernirelandUserNumbers["dup"] - northernirelandUserNumbers["sdlp"] - northernirelandUserNumbers["sinnfein"] - northernirelandUserNumbers["uu"] - northernirelandUserNumbers["alliance"];
      var percentages = northernirelandUserNumbers;
    }

    var percentageTotal = 0;




    var oldPartyTotals = {};
    var partyChange = {};

    for (party in percentages){
      var sum = 0;
      var total = 0;

      for (area in regions[region]){
        sum += parseInt(previousTotals[regions[region][area]][party]);
        total += parseInt(previousTotals[regions[region][area]]["turnout2010"]);
      }

      oldPartyTotals[party] = 100 * sum / parseFloat(total);
    }


    for (party in percentages) {
      partyChange[party] = percentages[party] - oldPartyTotals[party];
     }


    for (area in regions[region]){

      var newPartyTotals = {};

      sum = 0

      for (party in percentages){


        var oldPartyPercentage = 100 * previousTotals[regions[region][area]][party]  / parseFloat(previousTotals[regions[region][area]]["turnout2010"]);
        var newPartyPercentage = oldPartyPercentage + partyChange[party];

        if (newPartyPercentage <= 0){
          newPartyPercentage = 0
          };

        newPartyTotals[party] = newPartyPercentage;
      };

      var sum = 0

      for (party in newPartyTotals){
        sum += newPartyTotals[party]
      };

      var normalise = sum / 100

      for (party in newPartyTotals){
        newPartyTotals[party] /= normalise
      };

      userRegionalTotals[regions[region][area]] = newPartyTotals
    };


    getRegionalChange(region, percentages, userRegionalTotals);

    alterMap(region);
  }
}

function getRegionalChange(region, percentages, userRegionalTotals){
  var regionalRelativeChanges = {};

  for (area in regions[region]){

    var relativeChangeComplete = {};

    for (party in percentages){
      var relativeChange = userRegionalTotals[regions[region][area]][party] / previousPercentages[regions[region][area]][party]
      relativeChangeComplete[party] = relativeChange;
    }

    regionalRelativeChanges[regions[region][area]] = relativeChangeComplete;

  }

  seatAnalysis(region, percentages, regionalRelativeChanges);

}



function seatAnalysis(region, percentages, regionalRelativeChanges){

  for (area in regions[region]){

    for (seat in seatData){


        if (oldSeatData[seat]["area"] == regions[region][area]){

          var newSeatData = {};

          for (party in percentages){


            var seatRelativeToArea = oldSeatData[seat][party] / previousPercentages[regions[region][area]][party];

            if (seatRelativeToArea == 0) {
              newSeatData[party] = 0
            }
            else {
              var distribute = regionalRelativeChanges[regions[region][area]][party] - 1;

              var seatchange = 1 + (distribute / Math.sqrt(seatRelativeToArea));

              if (seatchange < 0.15){
                seatchange = 0.15;
              }


              var newPercentage = seatchange * oldSeatData[seat][party]

              if (oldSeatData[seat]["incumbent"] == party){
                if (party == "conservative"){
                  newPercentage += 1
                }

                if (party == "libdems"){
                  newPercentage += 5
                }

                if (party == "ukip"){
                  newPercentage += 8
                }

                if (party == "green"){
                  newPercentage += 8
                }

                if (party == "labour"){
                  newPercentage += 2
                }

              }

              newSeatData[party] = newPercentage;
            }

          }



          var sumPercentages = 0

          for (party in newSeatData){
            sumPercentages += newSeatData[party]
          }

          var normaliser = sumPercentages / 100;

          for (party in newSeatData){
            newSeatData[party] /= normaliser
            seatData[seat][party] = newSeatData[party]

          }

          var sortable = [];

          for (var party in newSeatData) {
            sortable.push([party, newSeatData[party]])
          }
          sortable.sort(function(a, b) {return a[1] - b[1]});




          var maxParty = sortable.pop();
          var secondMaxParty = sortable.pop();

          seatData[seat]["party"] = maxParty[0];
          seatData[seat]["majority"] = maxParty[1] - secondMaxParty[1];


          if (maxParty == seatData[seat]["incumbent"]){
            seatData[seat]["change"] = "no";
          }
          else{
            seatData[seat]["change"] = "yes";
          }
        }

    }
  }
}

function alterMap(region){
  g.selectAll(".map")
    .attr("class", function(d) {
				return "map " + seatData[d.properties.name]["party"];	})
  alterVoteTotals(region);
}


  // update vote total arrays (and don't forget to reset them too)

function alterVoteTotals(region){
  if (region == "reset"){
    getInfoFromFiles();

  }

  else {
    getAlteredVotes("all");
    for (area in regions[region]){

      getAlteredVotes(regions[region][area]);
    }

    if (region != "northernireland"){
      getAlteredVotes("greatbritain");
    }
    if (region == "england"){
      getAlteredVotes("england");
    }
  }
  $("#selectareatotals option:eq(0)").prop("selected", true);
  // double check later if this works
  selectAreaInfo("country");
}


function getAlteredVotes(area){

    var areas = [];

    if (area == "all"){
      areas = regions["england"].concat(regions["scotland"]).concat(regions["wales"]).concat(regions["northernireland"]);
    }

    else if (area == "greatbritain"){
      areas = regions["england"].concat(regions["scotland"]).concat(regions["wales"]);
    }

    else if (area == "england"){
      areas = regions["england"];
    }
    else {
      areas.push(area);
    }

    // // for each party and total, generate code, seats, change, votepercent, votepercent change
    // arra yof objects
    // wipe original array first
    // // add undefined at end?
    var totalvotescast2010 = 0;
    var totalvotescast = 0;

    $.each(areas, function(region){
      totalvotescast2010 += previousTotals[areas[region]]["turnout2010"];
    })

    totalvotescast = parseInt(totalvotescast2010 * 1.02);

    totalseats = 0 ;

    var holdingArray = [];

    $.each(parties, function(party){
      info = {}
      var code = parties[party];
      var seatssum = 0;
      var change = 0;
      var votepercent = 0;
      var votepercentchange = 0;

      $.each(seatData, function(seat){

        if (areas.indexOf(seatData[seat]["area"]) > -1){

          if (seatData[seat]["party"] == parties[party]){
            seatssum += 1;
            totalseats += 1;
          }

          if (seatData[seat]["incumbent"] == parties[party]){
            change += 1;
          }

          votepercent += seatData[seat][parties[party]] * oldSeatData[seat]["turnout2010"]  * 1.02 / 100 ;
          votepercentchange += oldSeatData[seat][parties[party]] * oldSeatData[seat]["turnout2010"] / 100 ;

        }
      });

      votepercent =  parseFloat((100 * votepercent / parseFloat(totalvotescast)).toFixed(2));
      change = seatssum - change;

      votepercentchange = 100 * votepercentchange / parseFloat(totalvotescast2010);
      votepercentchange = parseFloat((votepercent - votepercentchange).toFixed(2));



      info["code"] = code;
      info["seats"] = seatssum;
      info["change"] = change;
      info["votepercent"] = votepercent;
      info["votepercentchange"] = votepercentchange;

      holdingArray.push(info);


    });

    var totals = {"code": "total", "seats" : totalseats, "change": "", "votepercent" : 100.00, "votepercentchange" : ""};
    var stupidcsvextrarow = {"code": "", "seats" : undefined, "change": undefined, "votepercent" : undefined, "votepercentchange" : undefined};


    holdingArray.push(totals);
    holdingArray.push(stupidcsvextrarow);




    alterTable(area, holdingArray);

}


function alterTable(area, holdingarray){

  if (area == "all"){
    nationalVoteTotals = holdingarray;
  }

  if (area == "greatbritain"){
    greatbritainVoteTotals = holdingarray;
  }

  if (area == "england"){
    englandVoteTotals = holdingarray;
  }

  if (area == "scotland"){
    scotlandVoteTotals = holdingarray;
  }

  if (area == "wales"){
    walesVoteTotals = holdingarray;
  }

  if (area == "northernireland"){
    northernirelandVoteTotals = holdingarray;
  }

  if (area == "northeastengland"){
    northeastenglandVoteTotals = holdingarray;
  }

  if (area == "northwestengland"){
    northwestenglandVoteTotals = holdingarray;
  }

  if (area == "westmidlands"){
    westmidlandsVoteTotals = holdingarray;
  }


  if (area == "eastmidlands"){
    eastmidlandsVoteTotals = holdingarray;
  }

  if (area == "yorkshireandthehumber"){
    yorkshireandthehumberVoteTotals = holdingarray;
  }

  if (area == "eastofengland"){
    eastofenglandVoteTotals = holdingarray;
  }

  if (area == "southeastengland"){
    southeastenglandVoteTotals = holdingarray;
  }

  if (area == "southwestengland"){
    southwestenglandVoteTotals = holdingarray;
  }

  if (area == "london"){
    londonVoteTotals = holdingarray;
  }
}

// reset button for user input, reset user input states too
function resetInputs(){

  parseData("/election2015/data/info.csv", getSeatInfoAgain);
  $("#englandinput").get(0).reset()
  $("#scotlandinput").get(0).reset()
  $("#walesinput").get(0).reset()
  $("#northernirelandinput").get(0).reset()

  englandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
  scotlandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0, "snp" : 0 };
  walesUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "plaidcymru" : 0} ;
  northernirelandUserNumbers = {"dup" : 0, "sinnfein": 0, "sdlp": 0, "uu": 0, "alliance" : 0} ;

  nationalVoteTotals = [];
  greatbritainVoteTotals = [];
  englandVoteTotals = [];
  scotlandVoteTotals = [];
  walesVoteTotals = [];
  northernirelandVoteTotals = [];
  northeastenglandVoteTotals = [];
  northwestenglandVoteTotals = [];
  westmidlandsVoteTotals = [];
  eastmidlandsVoteTotals = [];
  yorkshireandthehumberVoteTotals = [];
  eastofenglandVoteTotals = [];
  southeastenglandVoteTotals = [];
  southwestenglandVoteTotals = [];
  londonVoteTotals = [];
}


// deal with annoyances with various browsers for input forms
var isFirefox = typeof InstallTrigger !== 'undefined';
var isIE = /*@cc_on!@*/false || !!document.documentMode;

$(document).ready(function(){
  if (isFirefox == true){
    $(".inputnumbers").css("width", "20px");
    $("#userinput p").css("margin-bottom", "1px");
    $("#resetinputs").css("padding-top", "8px");
  }

  if (isIE == true){
    $(".submitbutton").css("font-size", "0.85em");
    $("#userinput h4").css("margin-top", "-2px");
  }
});
