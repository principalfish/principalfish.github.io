var oldSeatData = {};
var previousYear = String(parseInt(pageSetting.slice(0, 4) -5));


function getOldInfo(data){
	$.each(data, function(seat){

		var to_add = {};
		var othersPercent = 0;
		$.each(parties, function(i){
			var party = parties[i];

			if (data[seat].party_info[party] != undefined){
				if (party == "other" || party == "others"){
					othersPercent += data[seat].party_info[party]["percent"];
				}
				else {
					to_add[party] = data[seat].party_info[party]["percent"];
				}
			}
		})

		oldSeatData[seat] = to_add;
		oldSeatData[seat]["other"] = othersPercent;
		oldSeatData[seat]["area"] = 	data[seat].seat_info["area"]
		oldSeatData[seat]["incumbent"] = 	data[seat].seat_info["winning_party"]

	});

}

function userInputCheck(inputform, country){

  var otherPercentage = sumFormPercentages(inputform).toFixed(0);

  spanId = "#other" + country;

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

	var otherPercentage = 100 - sum
	return otherPercentage
}

var englandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0};
var scotlandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "snp" : 0 };
var walesUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "plaidcymru" : 0} ;
var northernirelandUserNumbers = {"dup" : 0, "sinnfein": 0, "sdlp": 0, "uu": 0, "alliance" : 0};

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


	projection_getChange(percentages, region);

	alterMap(region);
	}

}

function projection_getChange(percentages, region){
	var partyChange = {};
	var userRegionalTotals = {};
	var previousPercentages = {};

	for (party in percentages){
		var previousPartyTotal = previousTotalsByYearByParty[previousYear][region][party];
		var previousRegionalTotal = previousTotalsByYearByParty[previousYear][region]["turnout"];
		var previousPartyPercentage = 100 * parseFloat(previousPartyTotal) / previousRegionalTotal;
		partyChange[party] = percentages[party] - previousPartyPercentage;
	}

	for (area in regions[region]){
			var newPartyTotals = {};

			sum = 0;

			for (party in percentages){

				var oldPartyPercentage = 100 * previousTotalsByYearByParty[previousYear][regions[region][area]][party]  / parseFloat(previousTotalsByYearByParty[year][regions[region][area]]["turnout"]);
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

		projection_getRegionalChange(region, percentages, userRegionalTotals);

}

function projection_getRegionalChange(region, percentages, userRegionalTotals){
	var regionalRelativeChanges = {};

	var previousPercentages = {};

	for (area in regions[region]){

		var relativeChangeComplete = {};

		var percentagesToAdd = {};

		for (party in percentages){

			var oldPartyPercentage = 100 * previousTotalsByYearByParty[previousYear][regions[region][area]][party]  / parseFloat(previousTotalsByYearByParty[year][regions[region][area]]["turnout"]);
			percentagesToAdd[party] = oldPartyPercentage
			var relativeChange = userRegionalTotals[regions[region][area]][party] / oldPartyPercentage;
			relativeChangeComplete[party] = relativeChange;
		}

		previousPercentages[regions[region][area]] = percentagesToAdd;

		regionalRelativeChanges[regions[region][area]] = relativeChangeComplete;

	}

	projection_seatAnalysis(region, percentages, regionalRelativeChanges, previousPercentages);

}

function projection_seatAnalysis(region, percentages, regionalRelativeChanges, previousPercentages){

	for (area in regions[region]){

		for (seat in seatData){

				if (oldSeatData[seat]["area"] == regions[region][area]){

					var newSeatData = {};

					for (party in percentages){

						var previousSeatPercent = oldSeatData[seat][party];

						var previousRegionalPercent = previousPercentages[regions[region][area]][party];
						var seatRelativeToArea = previousSeatPercent / previousRegionalPercent;

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
									newPercentage += 4;
								}

								if (party == "libdems"){
									newPercentage += 4;
								}

								if (party == "ukip"){
									newPercentage += 8;
								}

								if (party == "green"){
									newPercentage += 8;
								}

								if (party == "labour"){
									newPercentage += 1;
								}

								if (party == "snp"){
									newPercentage += 0;
								}

								if (party == "plaidcymru"){
									newPercentage += 4;
								}

							}

							if (isNaN(newPercentage)){
								newPercentage = 0;
							}
							newSeatData[party] = newPercentage;
						}
					}

					var sumPercentages = 0;

					for (party in newSeatData){
						sumPercentages += newSeatData[party];
					}

					var normaliser = sumPercentages / 100;

					for (party in newSeatData){
						newSeatData[party] /= normaliser;
						//seatData[seat][party] = newSeatData[party];
					}

					var sortable = [];

					for (party in newSeatData) {
						sortable.push([party, newSeatData[party]]);
					}
					sortable.sort(function(a, b) {return a[1] - b[1]});

					var maxParty = sortable.pop();
					var secondMaxParty = sortable.pop();

					var winningParty = maxParty[0];
					var majority = maxParty[1] - secondMaxParty[1];
					var change;

					if (maxParty == seatData[seat]["incumbent"]){
						change = "hold";
					}
					else{
						change = "gain";
					}

					seatData[seat]["seat_info"]["change"] = change;
					seatData[seat]["seat_info"]["winning_party"] = winningParty;
					seatData[seat]["seat_info"]["maj"] = parseInt(majority * seatData[seat]["seat_info"]["turnout"] / 100)
					seatData[seat]["seat_info"]["maj_percent"] = parseFloat(majority.toFixed(2));

					for (party in newSeatData){
						if (newSeatData[party] > 0){
							var partyChange = newSeatData[party] - oldSeatData[seat][party];
							var newPartyPercent = newSeatData[party];
							var newPartyTotal = newSeatData[party] * seatData[seat]["seat_info"]["turnout"] / 100 ;

							seatData[seat]["party_info"][party]["change"] = parseFloat(partyChange.toFixed(2));
							seatData[seat]["party_info"][party]["percent"] = parseFloat(newPartyPercent.toFixed(2));
							seatData[seat]["party_info"][party]["total"] = parseInt(newPartyTotal);
						}
					}
				}
		}
	}
}

function alterMap(region){
	seatsAfterFilter = [];
	totalElectorate = 0;
	$(".map").remove();
	getSeatInfo(seatData);
}

function resetInputs(){
	$("#englandinput").get(0).reset()
	$("#scotlandinput").get(0).reset()
	$("#walesinput").get(0).reset()
	$("#northernirelandinput").get(0).reset()
	$("#otherengland").text("100")
	$("#otherscotland").text("100")
	$("#otherwales").text("100")
	$("#othernorthernireland").text("100")

	englandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	scotlandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0, "snp" : 0 };
	walesUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "plaidcymru" : 0} ;
	northernirelandUserNumbers = {"dup" : 0, "sinnfein": 0, "sdlp": 0, "uu": 0, "alliance" : 0} ;


	loadTheMap(pageSetting);
}
