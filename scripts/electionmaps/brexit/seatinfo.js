var seatInfoTable = {
  // previous display state of table
  party : null,

  display : function(seat){

    var activeSeat = currentMap.seatData[seat];

    var votes =  [
      {"party" : "remain", "percent" : activeSeat.remain},
      {"party" : "leave", "percent" : activeSeat.leave}
    ];

    votes.sort(function(a, b){
      return b.percent - a.percent;
    });

    seatInfoTable.seatInfo(seat, activeSeat); // draw tables with data
    seatInfoTable.voteTable(votes); // draw table - do first since votes altered
    seatInfoTable.barChart(votes); // draw bar chart with data
  },

  seatInfo : function(seat, data){

    // clean old divs
    $("#information-party .party-flair").removeClass(seatInfoTable.party);


    $("#information-seatname-span").text(seat);
    $("#information-region").text(regionlist[data.region]);

    $("#information-party .party-name").text(partylist[data.winner2015]);
    $("#information-party .party-flair").addClass(data.winner2015);

    $("#information-majority").text("");

    $("#information-turnout").text("");

    // set for next time
    seatInfoTable.party = data.winner2015;

  },

  barChart : function(votes){
    $("#information-bar").empty();


    var dataitems = votes.length;
    var margin = {top: 10, right: 0, bottom: 10, left: 25};

  	var width = 200 - margin.left - margin.right;
  	var height = 200 - margin.top - margin.bottom;
  	var bargap = 2;
  	var barwidth = d3.min([90, (width / dataitems) - bargap]);
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

  	var max_of_votes = d3.max(votes, function(d) { return d.percent / 1 ; });

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
        .attr("y", function(d) { return y(d.percent); })
        .attr("height", function(d) { return height - y(d.percent ); });
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
      toAdd += "<div>" + partylist[votes[i].party] + "</div>"

      toAdd += "<div>" + votes[i].percent.toFixed(2) + "%</div>";

      toAdd += "</div>";

      $("#information-chart").append(toAdd);
    }
  }
};
