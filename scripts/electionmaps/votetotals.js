var voteTotals = {

  data : [],
  totals : {},

  electorate : {},
  turnout : null,

  calculate : function(region){

    if (region == "null"){
      region = "unitedkingdom"
    }

    // reset
    voteTotals.data =[];
    voteTotals.electorate = 0;

    if (region in regionMap){
      var regions = regionMap[region];
    } else {
      var regions = [region];
    }
    // get seats in regions selected
    var relevantSeats = [];

    for (var seat in currentMap.seatData){
      if (regions.indexOf(currentMap.seatData[seat].seatInfo.region) != -1 ){
        relevantSeats.push(seat);
        voteTotals.electorate += currentMap.seatData[seat].seatInfo.electorate;
      }
    }

    //totals object to add at end
    var totals = {
      "name" : "total",
      "seats" : relevantSeats.length,
      "change" : null,
      "votes" : 0,
      "oldvotes" :0,
      "votePercent" : null,
      "votePercentChange" : null
    }

    var partyTotals = {};
    // party, seats, seat change, votes, vote %, vote % change
    $.each(partylist, function(party, value){

      var toAdd = {
        "party" : party,
        "name" : value,
        "seats" : 0,
        "change" : 0,
        "votes" : 0,
        "oldvotes" : 0,
        "votePercent" : null,
        "votePercentChange" : null
      };

      $.each(relevantSeats, function(i, seat){

        // seats
        if (currentMap.seatData[seat].seatInfo.current == party){
          toAdd["seats"] += 1
        }
        //change
        if (currentMap.previousSeatData[seat].seatInfo.current == party){
          toAdd["change"] += 1
        }

        // vote totals
        if (party in currentMap.seatData[seat].partyInfo){
          totals["votes"] += currentMap.seatData[seat].partyInfo[party].total;
          toAdd["votes"] += currentMap.seatData[seat].partyInfo[party].total;
        }
        // get previous votes for change
        if (currentMap.election == true){
            if (party in currentMap.previousSeatData[seat].partyInfo){
              totals["oldvotes"] += currentMap.previousSeatData[seat].partyInfo[party].total;
              toAdd["oldvotes"] += currentMap.previousSeatData[seat].partyInfo[party].total;
            }
        }

      });

      toAdd["change"] = toAdd["seats"] - toAdd["change"];


      // for change from 2015 in 600 seat map
      if (currentMap.name == "2017-600seat"){
        var previousSeatTotal = 0;
        $.each(regions, function(i, region){
          if (party in seatsPerRegion2015[region]){
            previousSeatTotal += seatsPerRegion2015[region][party];
          }

        })
        toAdd["change"] = toAdd["seats"] - previousSeatTotal;
      }

      partyTotals[party] = toAdd;
    });

    voteTotals.totals = totals;

    // vote percentage
    $.each(partylist, function(party, value){
      partyTotals[party].votePercent = (100 * partyTotals[party].votes / totals.votes).toFixed(2) / 1;
      // for election maps show change from previous election (or state pre election?)
      if (currentMap.election == true){
        var oldPercent =  (100 * partyTotals[party].oldvotes / totals.oldvotes).toFixed(2) / 1 ;
        partyTotals[party].votePercentChange = (partyTotals[party].votePercent - oldPercent).toFixed(2) / 1;
      }
    });

    //set turnout
    voteTotals.turnout = (100 * totals.votes / voteTotals.electorate).toFixed(2) / 1;

    // combine other and others
    Object.keys(partyTotals["other"]).forEach(function(key){
      if (key != "name" && key != "party"){

        partyTotals["other"][key] += partyTotals["others"][key]
      }
    });
    // round others vote percent change
    partyTotals["other"].votePercentChange = (partyTotals["other"].votePercentChange.toFixed(2) / 1);
    // remove others
    delete partyTotals["others"];

    // add party totals to data array - filter irrelevance
    $.each(partyTotals, function(party, votedata){
      if (votedata.votes > 0){
        voteTotals.data.push(votedata);
      }
    })
    // set div title span
    $("#votetotalsarea").text(regionlist[region]);

    // reset sort state
    voteTotals.sortState = {
        "votes" : "desc",
        "seats" : "desc",
        "votePercent" : "desc",
        "votePercentChange" : "desc"
      };

    voteTotals.tableSortHandler("votes");
  },

  reorder : function(parameter, sorttype){
    if (sorttype == "asc"){
      voteTotals.data.sort(function(a, b){
        return a[parameter] - b[parameter];
      });
    } else if (sorttype == "desc"){
      voteTotals.data.sort(function(a, b){
        return b[parameter] - a[parameter];
      });
    }
    //display
    voteTotals.display();
  },

  activeSort : "votes",

  sortState : {},

  tableSortHandler: function(parameter){
    // remove class from old parameter
    $("#votetotals-sort" + voteTotals.activeSort).removeClass("sort-active");

    voteTotals.activeSort = parameter;
    voteTotals.reorder(voteTotals.activeSort, voteTotals.sortState[voteTotals.activeSort]);
    // UI CHANGE
    $("#votetotals-sort" + parameter).addClass("sort-active");

    // swap asc and desc
    if (voteTotals.sortState[parameter] == "desc"){
      voteTotals.sortState[parameter] = "asc";
    } else if (voteTotals.sortState[parameter] == "asc"){
      voteTotals.sortState[parameter] = "desc";
    }
  },

  display: function(){
    // reset div
    $("#votetotals-table-data").empty();
    // display turnout for all but current parliament
    
    if (currentMap.name == "election2015" 
      || currentMap.name == "election2010"
      || currentMap.name == "election2017"
      || currentMap.name == "election2019"){
      $("#votetotals-turnout").text("Turnout: " + voteTotals.turnout + "%");
    } else {
        $("#votetotals-turnout").text(" ");
    }

    var divider = "</div><div>";

    $.each(voteTotals.data, function(i, totals){
      var party = totals.party;
      var name = "<div class='party-flair " + party + "'></div>" + totals.name;
      var seats = totals.seats;
      var change = totals.change;
      var votes = parseInt(totals.votes);

      var percent = totals.votePercent.toFixed(2);
      var percentChange = null;
      if (totals.votePercentChange != null) {
        var percentChange = totals.votePercentChange.toFixed(2);
      }
      // colourise seat change
      var changeDiv = divider;
      if (change > 0){
        changeDiv = "</div><div style='color: green'>"
        change = "+" + change;
      } else if (change < 0){
        changeDiv = "</div><div style='color: red'>"
      }

      //colourise vot change
      var percentChangeDiv;


      if (percentChange >= 0){
        percentChangeDiv = "</div><div style='color: green'>" + percentChange;
      } else if (percentChange < 0){
        percentChangeDiv = "</div><div style='color: red'>" + percentChange;
      }


      var toAdd = "<div><div>" + name + divider + seats + changeDiv + change + divider +
                votes.toLocaleString() + divider + percent + percentChangeDiv + "</div></div>";

      $("#votetotals-table-data").append(toAdd);
    });

    // add totals
    var totalsDivider = "</div><div class='lastrow'>";

    $("#votetotals-table-data").append("<div><div class='lastrow'>Totals"
      + totalsDivider + voteTotals.totals.seats + totalsDivider +  "&nbsp;" + totalsDivider
      + voteTotals.totals.votes.toLocaleString() + totalsDivider + "&nbsp;" + totalsDivider +
      "&nbsp;" + "</div></div>"
    );

    //display/hide vote total percentchange dependent on page setting
    var percentChangeDisplay = {
      true : "block",
      false : "none"
    };

    $("#votetotals-table-titles > div > div:nth-child(6)").css("display",  percentChangeDisplay[currentMap.election]);
    $("#votetotals-table-data > div > div:nth-child(6)").css("display",  percentChangeDisplay[currentMap.election]);
  },

  getMajority : function(){
    var max = 0;
    var leader = null;
    $.each(voteTotals.data, function(i, data){
      if (data.seats > max){
        max = data.seats;
        leader = data.name;
      }
    });
    var seats;
    if (currentMap.mapurl == "650map.json" || currentMap.mapurl == "650map_new.json"){
      seats = 650 ;
    } else if (currentMap.mapurl == "600map.json"){
      seats = 600;
    }

    var majorityThreshold =  (seats / 2 );

    if (max > majorityThreshold ){
      var majorityNum = (max - majorityThreshold) * 2;
      $("#majoritytitle").text(leader + " Majority: " + majorityNum);
    } else {
      $("#majoritytitle").text("Hung Parliament");
    }
  }
};
