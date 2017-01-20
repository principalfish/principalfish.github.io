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
		oldSeatData[seat]["area"] = 	data[seat].seat_info["area"];
		oldSeatData[seat]["incumbent"] = 	data[seat].seat_info["winning_party"];

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

  var sum = 0;
  $sumpercentages.each(function() {

    var value = Number($(this).val());
    if (isNaN(value)) {

      value = 0;

    }

    sum += value;
  });

	var otherPercentage = 100 - sum;
	return otherPercentage;
}

var englandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0};
var scotlandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "snp" : 0 };
var walesUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "plaidcymru" : 0} ;
var northernirelandUserNumbers = {"dup" : 0, "sinnfein": 0, "sdlp": 0, "uu": 0, "alliance" : 0};

var northeastenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0};
var northwestenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0};
var yorkshireandthehumberUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
var southeastenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
var southwestenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
var eastofenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
var eastmidlandsUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
var westmidlandsUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
var londonUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};

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

		if (region == "northeastengland"){
			$.each(northeastenglandUserNumbers, function(party){

        if (isNaN(northeastenglandUserNumbers[party])){

          northeastenglandUserNumbers[party] = 0;

        }

      });

      northeastenglandUserNumbers["other"] = 100 - northeastenglandUserNumbers["labour"] - northeastenglandUserNumbers["conservative"] - northeastenglandUserNumbers["libdems"] - northeastenglandUserNumbers["ukip"] - northeastenglandUserNumbers["green"];
      var percentages = northeastenglandUserNumbers;
		}

		if (region == "northwestengland"){
			$.each(northwestenglandUserNumbers, function(party){

        if (isNaN(northwestenglandUserNumbers[party])){

          northwestenglandUserNumbers[party] = 0;

        }

      });

      northwestenglandUserNumbers["other"] = 100 - northwestenglandUserNumbers["labour"] - northwestenglandUserNumbers["conservative"] - northwestenglandUserNumbers["libdems"] - northwestenglandUserNumbers["ukip"] - northwestenglandUserNumbers["green"];
      var percentages = northwestenglandUserNumbers;
		}

		if (region == "yorkshireandthehumber"){
			$.each(yorkshireandthehumberUserNumbers, function(party){

        if (isNaN(yorkshireandthehumberUserNumbers[party])){

          yorkshireandthehumberUserNumbers[party] = 0;

        }

      });

      yorkshireandthehumberUserNumbers["other"] = 100 - yorkshireandthehumberUserNumbers["labour"] - yorkshireandthehumberUserNumbers["conservative"] - yorkshireandthehumberUserNumbers["libdems"] - yorkshireandthehumberUserNumbers["ukip"] - yorkshireandthehumberUserNumbers["green"];
      var percentages = yorkshireandthehumberUserNumbers;
		}

		if (region == "southeastengland"){
			$.each(southeastenglandUserNumbers, function(party){

        if (isNaN(southeastenglandUserNumbers[party])){

          southeastenglandUserNumbers[party] = 0;

        }

      });

      southeastenglandUserNumbers["other"] = 100 - southeastenglandUserNumbers["labour"] - southeastenglandUserNumbers["conservative"] - southeastenglandUserNumbers["libdems"] - southeastenglandUserNumbers["ukip"] - southeastenglandUserNumbers["green"];
      var percentages = southeastenglandUserNumbers;
		}

		if (region == "southwestengland"){
			$.each(southwestenglandUserNumbers, function(party){

        if (isNaN(southwestenglandUserNumbers[party])){

          southwestenglandUserNumbers[party] = 0;

        }

      });

      southwestenglandUserNumbers["other"] = 100 - southwestenglandUserNumbers["labour"] - southwestenglandUserNumbers["conservative"] - southwestenglandUserNumbers["libdems"] - southwestenglandUserNumbers["ukip"] - southwestenglandUserNumbers["green"];
      var percentages = southwestenglandUserNumbers;
		}

		if (region == "eastofengland"){
			$.each(eastofenglandUserNumbers, function(party){

        if (isNaN(eastofenglandUserNumbers[party])){

          eastofenglandUserNumbers[party] = 0;

        }

      });

      eastofenglandUserNumbers["other"] = 100 - eastofenglandUserNumbers["labour"] - eastofenglandUserNumbers["conservative"] - eastofenglandUserNumbers["libdems"] - eastofenglandUserNumbers["ukip"] - eastofenglandUserNumbers["green"];
      var percentages = eastofenglandUserNumbers;
		}

		if (region == "eastmidlands"){
			$.each(eastmidlandsUserNumbers, function(party){

        if (isNaN(eastmidlandsUserNumbers[party])){

          eastmidlandsUserNumbers[party] = 0;

        }

      });

      eastmidlandsUserNumbers["other"] = 100 - eastmidlandsUserNumbers["labour"] - eastmidlandsUserNumbers["conservative"] - eastmidlandsUserNumbers["libdems"] - eastmidlandsUserNumbers["ukip"] - eastmidlandsUserNumbers["green"];
      var percentages = eastmidlandsUserNumbers;
		}

		if (region == "westmidlands"){
			$.each(westmidlandsUserNumbers, function(party){

        if (isNaN(westmidlandsUserNumbers[party])){

          westmidlandsUserNumbers[party] = 0;

        }

      });

      westmidlandsUserNumbers["other"] = 100 - westmidlandsUserNumbers["labour"] - westmidlandsUserNumbers["conservative"] - westmidlandsUserNumbers["libdems"] - westmidlandsUserNumbers["ukip"] - westmidlandsUserNumbers["green"];
      var percentages = westmidlandsUserNumbers;
		}

		if (region == "london"){
			$.each(londonUserNumbers, function(party){

        if (isNaN(londonUserNumbers[party])){

          londonUserNumbers[party] = 0;

        }

      });

      londonUserNumbers["other"] = 100 - londonUserNumbers["labour"] - londonUserNumbers["conservative"] - londonUserNumbers["libdems"] - londonUserNumbers["ukip"] - londonUserNumbers["green"];
      var percentages = londonUserNumbers;
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

					newPartyPercentage = 0;

					};

				newPartyTotals[party] = newPartyPercentage;

			};


			var sum = 0;

			for (party in newPartyTotals){
				sum += newPartyTotals[party];
			};


			var normalise = sum / 100;

			for (party in newPartyTotals){

				newPartyTotals[party] /= normalise;

			};

			userRegionalTotals[regions[region][area]] = newPartyTotals;


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
			percentagesToAdd[party] = oldPartyPercentage;
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

							newSeatData[party] = 0;

						}

						else {

							var distribute = regionalRelativeChanges[regions[region][area]][party] - 1;

							var seatchange = 1 + (distribute / Math.sqrt(seatRelativeToArea));

							if (seatchange < 0.15){

								seatchange = 0.15;

							}

							var newPercentage = seatchange * oldSeatData[seat][party];

							if (oldSeatData[seat]["incumbent"] == party){

								if (party == "conservative"){

									newPercentage += 2;

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
					seatData[seat]["seat_info"]["maj"] = parseInt(majority * seatData[seat]["seat_info"]["turnout"] / 100);
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
	$("#englandinput").get(0).reset();
	$("#scotlandinput").get(0).reset();
	$("#walesinput").get(0).reset();
	$("#northernirelandinput").get(0).reset();
	$("#otherengland").text("100");
	$("#otherscotland").text("100");
	$("#otherwales").text("100");
	$("#othernorthernireland").text("100");

	$("#northeastenglandinput").get(0).reset();
	$("#othernortheastengland").text("100");

	$("#northwestenglandinput").get(0).reset();
	$("#othernorthwestengland").text("100");

	$("#yorkshireandthehumberinput").get(0).reset();
	$("#otheryorkshireandthehumber").text("100");

	$("#southeastenglandinput").get(0).reset();
	$("#othersoutheastengland").text("100");


	$("#southwestenglandinput").get(0).reset();
	$("#othersouthwestengland").text("100");

	$("#eastofenglandinput").get(0).reset();
	$("#othereastofengland").text("100");


	$("#eastmidlandsinput").get(0).reset();
	$("#othereastmidlands").text("100");

	$("#westmidlandsinput").get(0).reset();
	$("#otherwestmidlands").text("100");

	$("#londoninput").get(0).reset();
	$("#otherlondon").text("100");

	englandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	scotlandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0, "snp" : 0 };
	walesUserNumbers = {"conservative": 0, "labour" : 0, "libdems" : 0, "ukip" : 0, "green": 0, "plaidcymru" : 0} ;
	northernirelandUserNumbers = {"dup" : 0, "sinnfein": 0, "sdlp": 0, "uu": 0, "alliance" : 0} ;

	northeastenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	northwestenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	yorkshireandthehumberUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	southeastenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	southwestenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	eastofenglandUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	eastmidlandsUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	westmidlandsUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};
	londonUserNumbers = {"conservative": 0, "labour" : 0, "libdems" :0, "ukip" : 0, "green": 0};

	loadTheMap(pageSetting);
}
