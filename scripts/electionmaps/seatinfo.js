var seatInfoTable = {
  // previous display state of table
  party : null,
  gain : null,

  display : function(seat,showTurnout){

    var activeSeatData = new activeSeat(seat);

    seatInfoTable.seatInfo(activeSeatData, showTurnout); // draw tables with data
    seatInfoTable.voteTable(activeSeatData.votes); // draw table - do first since votes altered
    seatInfoTable.barChart(activeSeatData.votes); // draw bar chart with data
  },

  seatInfo : function(data, showTurnout){
    // clean old divs
    $("#information-party .party-flair").removeClass(seatInfoTable.party);
    $("#information-gain .party-flair").removeClass(seatInfoTable.gain);

    if (data.byElection != undefined){
      data.name += "*";
      $("#information-byelection").text("*By-election on " + data.byElection);
    } else {
      $("#information-byelection").text("");
    }

    $("#information-seatname-span").text(data.name);
    $("#information-region").text(regionlist[data.region]);

    $("#information-party .party-name").text(partylist[data.current]);
    $("#information-party .party-flair").addClass(data.current);

    if (data.current != data.previous){
      $("#information-gain .party-name").text("FROM " + partylist[data.previous]); //
      $("#information-gain .party-flair").addClass(data.previous);
    } else {
      $("#information-gain .party-name").text("");
    }
    console.log(data)
    var majorityPercentage = (100 * data.majority / data.turnout).toFixed(2);
    var majorityTextString = "Majority: " + majorityPercentage + "%"
    if (showTurnout){
      majorityTextString += "= " + data.majority
    }
    $("#information-majority").text(majorityTextString);

    var turnoutPercentage = (100 * data.turnout / data.electorate).toFixed(2);
    if (showTurnout) {
      $("#information-turnout").text("Turnout : " + data.turnout + " = " + turnoutPercentage + "%" );
    } else {
      $("#information-turnout").text("")
    }
   

    // set for next time
    seatInfoTable.party = data.current;
    seatInfoTable.gain = data.previous;
  },

  barChart : function(votes){
    $("#information-bar").empty();

    var relevantVotes = []
    // trim the fat
    for (var i=0; i < votes.length; i++){
        if (votes[i].totalPercentage > 5){
          relevantVotes.push(votes[i])
        }
    }

    votes = relevantVotes;

    var dataitems = votes.length;
    var margin = {top: 10, right: 0, bottom: 10, left: 25};

  	var width = 200 - margin.left - margin.right;
  	var height = 200 - margin.top - margin.bottom;
  	var bargap = 2;
  	var barwidth = d3.min([60, (width / dataitems) - bargap]);
  	var animationdelay = 250;

    var svg1 = d3.select("#information-bar")
    		.append("svg")
    			.attr("width", width + margin.left + margin.right)
    			.attr("height", height + margin.top + margin.bottom)
    		.append("g")
        	.attr("transform", "translate(" + margin.left + "," + margin.top + ")");

  	var x = d3.scaleOrdinal().range([0, dataitems * (barwidth + 2)]);

  	var xAxis = d3.axisBottom()
  	    .scale(x);

  	var y = d3.scaleLinear()
  		.range([height, 0]);

  	var yAxis = d3.axisLeft()
      .scale(y)
      .ticks(6);

  	var max_of_votes = d3.max(votes, function(d) { return d.totalPercentage / 1 ; });

  	y.domain([0, d3.max([60, (max_of_votes + (10 - max_of_votes % 10))])]);

    svg1.append("g")
      .attr("class", "x axis")
      .attr("transform", "translate(0," + height + ")")
      .call(xAxis);

  	svg1.append("g")
      .attr("class", "y axis")
      .call(yAxis);

  	svg1.selectAll("rect")
  		.data(votes)
  		.enter()
  		.append("rect")
        .attr("x", function(d, i) { return bargap * (i + 1) + barwidth * (i); })
        .attr("width", barwidth)
  			.attr("y", height)
        .attr("height", 0)
  			.attr("class", function(d) { return d.party;})
  		.transition()
        .delay(function(d, i) { return i * animationdelay / 2; })
        .duration(animationdelay)
        .attr("y", function(d) { return y(d.totalPercentage); })
        .attr("height", function(d) { return height - y(d.totalPercentage ); });
  },

  voteTable : function(votes){
    $("#information-chart").empty();

    // see if any changes
    for (var i=0; i < votes.length; i++){

      // add divs - styled in electionmap.scss
      var toAdd = "<div class='party-info-table'>";
      // add party-block
      toAdd += "<div class='party-flair " + votes[i].party + "'></div>"
      // add mp name
      toAdd += "<div>" + votes[i].name + "</div>"

      // add vote percentage     // if change add change

      var voteChange = "";
      var voteChangeColour;
      if (votes[i].change != 0){
        voteChange = "(" + votes[i].change + ")";

        if (votes[i].change > 0) {
          voteChangeColour = "green";
        } else {
          voteChangeColour = "red";
        }
        voteChange = "<span style='color: " + voteChangeColour + ";'>" + voteChange + "</span>"

      }


      toAdd += "<div>" + votes[i].totalPercentage + "% " + voteChange + "</div>";
    //  toAdd += "<div>" + voteChange + "</div>";
      // complete divs
      toAdd += "</div>";

      $("#information-chart").append(toAdd);
    }
  }
};

function activeSeat(seat){
  this.name = seat;

  var seatInfo = currentMap.seatData[seat]["seatInfo"];
  var partyInfo = currentMap.seatData[seat]["partyInfo"];

  this.region = seatInfo.region;

  this.current = seatInfo.current;
  this.previous = currentMap.previousSeatData[seat].seatInfo.current

  this.electorate = seatInfo.electorate;

  this.turnout = (seatInfo.turnout.toFixed(0)) / 1;

  this.majority = seatInfo.majority;
  //  this.turnoutPercentage = seatInfo.percentage_turnout; = turnout / electorate x 100
  // this.majorityPercent = seatInfo.maj_percent; = majority / turnout

  this.byElection = seatInfo.byElection;

  this.votes = [];

  var previousPartyInfo = currentMap.previousSeatData[seat]["partyInfo"];

  var previousTurnout = 0;
  $.each(previousPartyInfo, function(party, info){
    previousTurnout += info.total;
  });

  for (var party in partyInfo){
    if (partyInfo[party].total > 0){
      // get previous total if it exists
      var previousTotalPercentage;
      if (party in previousPartyInfo){
        var previousTotal = previousPartyInfo[party].total;

        previousTotalPercentage =  (100 * previousTotal /  previousTurnout).toFixed(2);

      } else {
        previousTotalPercentage = 0;
      }



      partyData = partyInfo[party];
      var totalPercentage = (100 * partyData.total / this.turnout).toFixed(2);
      var change = (totalPercentage - previousTotalPercentage).toFixed(2);
      if (party == "other" || party == "others"){
        var change = "0";
      }

      this.votes.push({
        "party" : party,
        "name" : partyData.name,
      //  "percentage" : partyData.percent, = total / turnout
        "totalPercentage" : totalPercentage,
        "previousTotalPercentage" : previousTotalPercentage,
        "change" : change
      });
    }
  }

    // sort vote totals
  this.votes.sort(function(a, b){
    return b.totalPercentage - a.totalPercentage;
  });

}
